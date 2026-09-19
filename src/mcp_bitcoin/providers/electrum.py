"""Electrum protocol backend (ElectrumX / Fulcrum)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
from typing import Any
from urllib.parse import urlparse

from embit.transaction import Transaction

from mcp_bitcoin.config import Network, ProviderType
from mcp_bitcoin.providers.base import ProviderError
from mcp_bitcoin.providers.rest import (
    VSIZE_1IN_1OUT_P2WPKH,
    VSIZE_2IN_2OUT_P2WPKH,
    vsize_from_weight,
)
from mcp_bitcoin.types import (
    UTXO,
    Balance,
    BlockHeader,
    DecodedTransaction,
    FeeEstimate,
    TxHistoryPage,
    TxInput,
    TxOutput,
    TxSummary,
)
from mcp_bitcoin.utils.units import sats_to_btc
from mcp_bitcoin.utils.validation import validate_address, validate_txid

logger = logging.getLogger(__name__)

CLIENT_NAME = "mcp-bitcoin"
PROTOCOL_VERSION = "1.4"
HEADERS_SUBSCRIBE = "blockchain.headers.subscribe"

DEFAULT_PORTS = {"ssl": 50002, "tcp": 50001}


def script_hash_for(address: str, network: Network) -> str:
    """Convert an address to an Electrum script hash.

    Electrum indexes by ``sha256(scriptPubKey)`` reversed. Because the same
    pubkey hash yields an identical script on every network, the address must
    be network-checked *before* this point or a mainnet address would silently
    return a testnet balance.
    """
    script = validate_address(address, network)
    return hashlib.sha256(script.data).digest()[::-1].hex()


class ElectrumProvider:
    """Electrum protocol backend over TCP or SSL.

    Responses are matched by JSON-RPC ``id``. Servers also push unsolicited
    subscription notifications, which have no ``id``; those are consumed and
    used to keep the chain tip current instead of being mistaken for replies.
    """

    provider_type = ProviderType.ELECTRUM
    name = "electrum"

    def __init__(
        self,
        url: str,
        network: Network,
        tor_proxy: str | None = None,
        allow_self_signed: bool = False,
    ):
        self.url = url
        self.network = network
        self._tor_proxy = tor_proxy
        self._allow_self_signed = allow_self_signed
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._id = 0
        self._lock = asyncio.Lock()
        self._tip_height: int | None = None

        parsed = urlparse(url)
        if parsed.scheme not in DEFAULT_PORTS:
            raise ValueError(
                f"MCP_BITCOIN_ELECTRUM_URL must start with ssl:// or tcp://, got "
                f"{url!r}. Without a scheme the host cannot be parsed reliably."
            )
        if not parsed.hostname:
            raise ValueError(f"Could not parse a host from {url!r}")

        self._host = parsed.hostname
        self._port = parsed.port or DEFAULT_PORTS[parsed.scheme]
        self._use_ssl = parsed.scheme == "ssl"

    # -- connection --------------------------------------------------------

    @property
    def _is_onion(self) -> bool:
        return self._host.endswith(".onion")

    def _ssl_context(self) -> ssl.SSLContext | None:
        """Build the TLS context, verifying certificates unless told otherwise.

        Many Electrum servers use self-signed certificates, so verification has
        to be defeatable — but it is *not* defeated by default, because an
        unverified TLS session to a balance/UTXO source is exactly the position
        from which a man-in-the-middle can feed fabricated financial data.

        The one case where verification is genuinely redundant is a ``.onion``
        host: a v3 onion address *is* the server's public key, so the Tor
        circuit already authenticates the endpoint cryptographically.
        """
        if not self._use_ssl:
            return None

        ctx = ssl.create_default_context()
        if self._is_onion:
            logger.debug(
                "Skipping certificate verification for %s — the onion address "
                "authenticates the endpoint",
                self._host,
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif self._allow_self_signed:
            logger.warning(
                "Certificate verification DISABLED for Electrum server %s. "
                "This connection is encrypted but not authenticated.",
                self._host,
            )
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def _open_socket(self) -> None:
        try:
            await self._open_socket_inner()
        except ssl.SSLCertVerificationError as exc:
            raise ProviderError(
                self.name,
                f"TLS certificate verification failed for {self._host}: {exc.verify_message}. "
                "Electrum servers commonly use self-signed certificates. If you "
                "trust this server (for example your own node on your LAN), set "
                "MCP_BITCOIN_ELECTRUM_ALLOW_SELF_SIGNED=true — but note that this "
                "leaves the connection encrypted yet unauthenticated, so a "
                "man-in-the-middle could return fabricated balances. Connecting "
                "over a .onion address avoids the trade-off entirely.",
            ) from exc

    async def _open_socket_inner(self) -> None:
        ssl_ctx = self._ssl_context()
        if self._tor_proxy:
            try:
                from python_socks.async_.asyncio import Proxy
            except ImportError as exc:  # pragma: no cover - dependency is declared
                raise ProviderError(
                    self.name,
                    "python-socks is required for Electrum over Tor; "
                    "reinstall mcp-bitcoin to pull it in",
                ) from exc
            proxy = Proxy.from_url(self._tor_proxy)
            sock = await proxy.connect(dest_host=self._host, dest_port=self._port)
            self._reader, self._writer = await asyncio.open_connection(
                sock=sock,
                ssl=ssl_ctx,
                server_hostname=self._host if self._use_ssl else None,
            )
        else:
            self._reader, self._writer = await asyncio.open_connection(
                self._host, self._port, ssl=ssl_ctx
            )

    async def _connect(self) -> None:
        """Open a session, negotiate the protocol and subscribe to headers.

        ElectrumX and Fulcrum expect ``server.version`` as the first message of
        a session; doing it here rather than in ``ping`` means every code path
        gets a negotiated connection.
        """
        if self._reader is not None and self._writer is not None:
            return
        await self._open_socket()
        try:
            await self._send("server.version", [CLIENT_NAME, PROTOCOL_VERSION])
            header = await self._send(HEADERS_SUBSCRIBE, [])
            if isinstance(header, dict):
                self._tip_height = header.get("height")
        except Exception:
            await self._reset()
            raise

    async def _reset(self) -> None:
        """Tear down the connection so the next call reconnects cleanly.

        Every error path funnels through here. Leaving a half-dead socket in
        place was what allowed one request's reply to be served as the answer
        to the next one.
        """
        writer, self._writer, self._reader = self._writer, None, None
        if writer is not None:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # -- framing -----------------------------------------------------------

    async def _send(self, method: str, params: list[Any]) -> Any:
        """Write one request and read until the reply with a matching id."""
        assert self._reader is not None and self._writer is not None
        self._id += 1
        request_id = self._id

        self._writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params,
                    }
                )
                + "\n"
            ).encode()
        )
        await self._writer.drain()

        while True:
            line = await self._reader.readline()
            if not line:
                raise ProviderError(self.name, "connection closed by server")
            try:
                message = json.loads(line)
            except ValueError as exc:
                raise ProviderError(self.name, f"malformed response: {exc}") from exc

            # Unsolicited notification: no id, carries a method name instead.
            if "id" not in message or message.get("id") is None:
                self._handle_notification(message)
                continue

            if message["id"] != request_id:
                logger.debug("Discarding stale response id=%s", message["id"])
                continue

            error = message.get("error")
            if error:
                detail = error.get("message", str(error)) if isinstance(error, dict) else str(error)
                raise ProviderError(self.name, detail)
            return message.get("result")

    def _handle_notification(self, message: dict[str, Any]) -> None:
        if message.get("method") == HEADERS_SUBSCRIBE:
            params = message.get("params") or []
            if params and isinstance(params[0], dict):
                height = params[0].get("height")
                if height is not None:
                    self._tip_height = height
                    logger.debug("Electrum tip advanced to %s", height)

    async def _request(self, method: str, params: list[Any] | None = None) -> Any:
        async with self._lock:
            try:
                await self._connect()
                return await self._send(method, params or [])
            except ProviderError:
                await self._reset()
                raise
            except (TimeoutError, asyncio.CancelledError):
                await self._reset()
                raise
            except Exception as exc:
                await self._reset()
                raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    # -- chain -------------------------------------------------------------

    async def ping(self) -> bool:
        await self._request("server.version", [CLIENT_NAME, PROTOCOL_VERSION])
        return True

    async def get_tip_height(self) -> int:
        if self._tip_height is None:
            header = await self._request(HEADERS_SUBSCRIBE)
            if isinstance(header, dict):
                self._tip_height = header.get("height")
        if self._tip_height is None:
            raise ProviderError(self.name, "server did not report a chain tip")
        return self._tip_height

    async def get_block_height(self) -> BlockHeader:
        height = await self.get_tip_height()
        return BlockHeader(
            hash="",
            height=height,
            timestamp=0,
            confirmations=1,
            source=self.name,
        )

    async def get_block(self, block_ref: str) -> BlockHeader:
        raise NotImplementedError(
            "Electrum servers do not serve full block data; use Bitcoin Core, "
            "mempool.space or Esplora"
        )

    def _confirmations(self, height: int | None, tip: int) -> int:
        return max(tip - height + 1, 0) if height and height > 0 else 0

    # -- address queries ---------------------------------------------------

    async def get_balance(self, address: str) -> Balance:
        sh = script_hash_for(address, self.network)
        data = await self._request("blockchain.scripthash.get_balance", [sh])
        confirmed = data.get("confirmed", 0)
        unconfirmed = data.get("unconfirmed", 0)
        total = confirmed + unconfirmed
        return Balance(
            address=address.strip(),
            confirmed_sats=confirmed,
            unconfirmed_sats=unconfirmed,
            total_sats=total,
            confirmed_btc=sats_to_btc(confirmed),
            unconfirmed_btc=sats_to_btc(unconfirmed),
            total_btc=sats_to_btc(total),
            source=self.name,
        )

    async def get_utxos(self, address: str) -> list[UTXO]:
        sh = script_hash_for(address, self.network)
        data = await self._request("blockchain.scripthash.listunspent", [sh])
        tip = await self.get_tip_height()
        return [
            UTXO(
                txid=u["tx_hash"],
                vout=u["tx_pos"],
                value_sats=u["value"],
                value_btc=sats_to_btc(u["value"]),
                confirmations=self._confirmations(u.get("height"), tip),
                block_height=u.get("height") if u.get("height", 0) > 0 else None,
            )
            for u in data
        ]

    async def get_tx_history(
        self, address: str, limit: int = 25, after_txid: str | None = None
    ) -> TxHistoryPage:
        """Return history newest-first.

        Electrum returns the full history oldest-first, unlike the Esplora
        family. Reversing here keeps ordering consistent across backends so a
        failover doesn't silently flip the answer.
        """
        sh = script_hash_for(address, self.network)
        data = await self._request("blockchain.scripthash.get_history", [sh])
        tip = await self.get_tip_height()

        entries = list(reversed(data))
        start = 0
        if after_txid:
            for i, item in enumerate(entries):
                if item.get("tx_hash") == after_txid:
                    start = i + 1
                    break

        window = entries[start : start + limit]
        transactions = [
            TxSummary(
                txid=item["tx_hash"],
                block_height=item.get("height") if item.get("height", 0) > 0 else None,
                confirmations=self._confirmations(item.get("height"), tip),
                fee_sats=item.get("fee"),
            )
            for item in window
        ]
        has_more = start + limit < len(entries)
        return TxHistoryPage(
            transactions=transactions,
            has_more=has_more,
            next_cursor=transactions[-1].txid if has_more and transactions else None,
            source=self.name,
        )

    # -- transactions ------------------------------------------------------

    async def get_transaction(self, txid: str) -> DecodedTransaction:
        txid = validate_txid(txid)
        raw = await self._request("blockchain.transaction.get", [txid, True])
        if isinstance(raw, str):
            # Server ignored the verbose flag; decode the hex ourselves.
            return self._decode_hex(raw, txid)

        inputs = [
            TxInput(
                txid=vin.get("txid"),
                vout=vin.get("vout"),
                is_coinbase="coinbase" in vin,
                sequence=vin.get("sequence"),
            )
            for vin in raw.get("vin", [])
        ]
        outputs = []
        for vout in raw.get("vout", []):
            spk = vout.get("scriptPubKey", {})
            address = spk.get("address")
            if not address and spk.get("addresses"):
                address = spk["addresses"][0]
            value = round(vout["value"] * 1e8)
            outputs.append(
                TxOutput(
                    n=vout["n"],
                    address=address,
                    value_sats=int(value),
                    value_btc=sats_to_btc(int(value)),
                    script_type=spk.get("type"),
                    script_hex=spk.get("hex"),
                )
            )

        return DecodedTransaction(
            txid=raw.get("txid", txid),
            version=raw.get("version", 0),
            locktime=raw.get("locktime", 0),
            size=raw.get("size"),
            vsize=raw.get("vsize") or vsize_from_weight(raw.get("weight"), raw.get("size")),
            weight=raw.get("weight"),
            is_segwit=raw.get("hash") != raw.get("txid"),
            inputs=inputs,
            outputs=outputs,
            total_output_sats=sum(o.value_sats for o in outputs),
            confirmations=raw.get("confirmations"),
            block_hash=raw.get("blockhash"),
            block_time=raw.get("blocktime"),
            source=self.name,
        )

    def _decode_hex(self, hex_str: str, txid: str) -> DecodedTransaction:
        raw = bytes.fromhex(hex_str)
        tx = Transaction.parse(raw)
        outputs = [
            TxOutput(
                n=i,
                address=self._safe_address(out.script_pubkey),
                value_sats=out.value,
                value_btc=sats_to_btc(out.value),
                script_hex=out.script_pubkey.data.hex(),
            )
            for i, out in enumerate(tx.vout)
        ]
        return DecodedTransaction(
            txid=txid,
            version=tx.version,
            locktime=tx.locktime,
            size=len(raw),
            is_segwit=tx.is_segwit,
            inputs=[
                TxInput(txid=vin.txid.hex(), vout=vin.vout, sequence=vin.sequence) for vin in tx.vin
            ],
            outputs=outputs,
            total_output_sats=sum(o.value_sats for o in outputs),
            source=self.name,
        )

    def _safe_address(self, script: Any) -> str | None:
        try:
            return str(script.address(network=self.network.embit_network))
        except Exception:
            return None

    async def get_raw_transaction(self, txid: str) -> str:
        raw: str = await self._request("blockchain.transaction.get", [validate_txid(txid), False])
        return raw

    # -- fees --------------------------------------------------------------

    async def estimate_fee(self, target_blocks: int) -> FeeEstimate:
        btc_per_kb = await self._request("blockchain.estimatefee", [target_blocks])
        if not isinstance(btc_per_kb, (int, float)) or btc_per_kb <= 0:
            raise ProviderError(
                self.name,
                f"server has no fee estimate for {target_blocks} blocks",
            )
        rate = float(btc_per_kb) * 1e8 / 1000
        return FeeEstimate(
            target_blocks=target_blocks,
            sat_per_vbyte=round(rate, 2),
            estimated_fee_p2wpkh_1in_1out_sats=round(rate * VSIZE_1IN_1OUT_P2WPKH),
            estimated_fee_p2wpkh_2in_2out_sats=round(rate * VSIZE_2IN_2OUT_P2WPKH),
            source=self.name,
        )

    async def get_fee_histogram(self) -> list[dict[str, Any]]:
        data = await self._request("mempool.get_fee_histogram")
        return [
            {"fee_rate_sat_vb": float(rate), "vsize": int(vsize)} for rate, vsize in (data or [])
        ]

    async def get_recommended_fees(self) -> Any:
        raise NotImplementedError(
            "Electrum servers expose per-target estimates but no recommended "
            "tiers; use mempool.space, Esplora or Bitcoin Core"
        )

    async def get_mempool_info(self) -> Any:
        raise NotImplementedError(
            "Electrum servers do not expose mempool totals; use Bitcoin Core or mempool.space"
        )

    async def get_mempool_entry(self, txid: str) -> Any:
        raise NotImplementedError(
            "Electrum servers do not expose per-transaction mempool entries; "
            "use Bitcoin Core or mempool.space"
        )

    async def close(self) -> None:
        await self._reset()
