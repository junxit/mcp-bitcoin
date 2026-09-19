"""Bitcoin Core / Knots JSON-RPC backend."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp
from aiohttp_socks import ProxyConnector

from mcp_bitcoin.config import Network, ProviderType
from mcp_bitcoin.providers.base import NetworkMismatchError, ProviderError
from mcp_bitcoin.providers.rest import (
    VSIZE_1IN_1OUT_P2WPKH,
    VSIZE_2IN_2OUT_P2WPKH,
)
from mcp_bitcoin.types import (
    BlockHeader,
    DecodedTransaction,
    FeeEstimate,
    FeeTiers,
    MempoolInfo,
    TxInput,
    TxOutput,
)
from mcp_bitcoin.utils.units import btc_to_sats, sats_to_btc
from mcp_bitcoin.utils.validation import validate_txid

logger = logging.getLogger(__name__)

# Core reports its chain as these strings, which match Network.embit_key.
CORE_CHAIN_NAMES = {"main", "test", "signet", "regtest", "testnet4"}

DEFAULT_COOKIE_PATHS = {
    Network.MAINNET: "~/.bitcoin/.cookie",
    Network.TESTNET: "~/.bitcoin/testnet3/.cookie",
    Network.SIGNET: "~/.bitcoin/signet/.cookie",
    Network.REGTEST: "~/.bitcoin/regtest/.cookie",
}


class BitcoinCoreProvider:
    """Bitcoin Core JSON-RPC backend.

    Core has no address index, so address-scoped queries raise
    ``NotImplementedError`` and the manager routes them to an Electrum or
    Esplora backend instead.
    """

    provider_type = ProviderType.CORE
    name = "core"

    def __init__(
        self,
        url: str,
        network: Network,
        tor_proxy: str | None = None,
        cookie_path: str | None = None,
    ):
        self.url = url
        self.network = network
        self._tor_proxy = tor_proxy
        self._session: aiohttp.ClientSession | None = None
        self._id = 0
        self._chain_verified = False

        parsed = urlparse(url if "://" in url else f"http://{url}")
        if not parsed.hostname:
            raise ValueError(
                f"Could not parse a host from MCP_BITCOIN_CORE_URL={url!r}. "
                'Expected something like "http://user:pass@127.0.0.1:8332".'
            )

        port = parsed.port or network.core_rpc_port
        # Preserve the path so multiwallet endpoints (/wallet/<name>) survive.
        self._rpc_url = urlunparse(
            (parsed.scheme or "http", f"{parsed.hostname}:{port}", parsed.path or "", "", "", "")
        )
        self._auth = self._resolve_auth(parsed, cookie_path)

    def _resolve_auth(self, parsed: Any, cookie_path: str | None) -> aiohttp.BasicAuth | None:
        """Prefer credentials in the URL, else fall back to Core's cookie file."""
        if parsed.username:
            return aiohttp.BasicAuth(parsed.username, parsed.password or "")

        candidate = cookie_path or DEFAULT_COOKIE_PATHS.get(self.network)
        if not candidate:
            return None
        path = Path(candidate).expanduser()
        try:
            user, _, password = path.read_text().strip().partition(":")
        except OSError:
            logger.debug("No Core cookie file at %s", path)
            return None
        logger.info("Using Bitcoin Core cookie auth from %s", path)
        return aiohttp.BasicAuth(user, password)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = ProxyConnector.from_url(self._tor_proxy) if self._tor_proxy else None
            self._session = aiohttp.ClientSession(connector=connector, auth=self._auth)
        return self._session

    async def _rpc(self, method: str, params: list[Any] | None = None) -> Any:
        self._id += 1
        payload = {
            "jsonrpc": "2.0",
            "id": self._id,
            "method": method,
            "params": params or [],
        }
        session = await self._get_session()
        try:
            async with session.post(self._rpc_url, json=payload) as resp:
                body = await resp.text()

                if resp.status in (401, 403):
                    raise ProviderError(
                        self.name,
                        "authentication failed (HTTP "
                        f"{resp.status}). Check rpcuser/rpcpassword, or let the "
                        "server read Core's .cookie file.",
                    )
                if resp.status >= 500 and not body.strip().startswith("{"):
                    raise ProviderError(self.name, f"HTTP {resp.status}: {body[:200]}")

                try:
                    import json

                    data = json.loads(body)
                except ValueError as exc:
                    raise ProviderError(
                        self.name,
                        f"non-JSON response (HTTP {resp.status}): {body[:200]}",
                    ) from exc

                error = data.get("error") if isinstance(data, dict) else None
                if error:
                    if isinstance(error, dict):
                        raise ProviderError(
                            self.name,
                            f"RPC error {error.get('code')}: {error.get('message')}",
                        )
                    raise ProviderError(self.name, f"RPC error: {error}")
                return data.get("result")
        except aiohttp.ClientError as exc:
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _verify_chain(self, info: dict[str, Any]) -> None:
        """Refuse to serve data from a node on a different chain."""
        if self._chain_verified:
            return
        chain = info.get("chain")
        expected = self.network.embit_key
        # Core calls mainnet "main"; older builds used "test" for all testnets.
        if chain and chain in CORE_CHAIN_NAMES and chain != expected:
            raise NetworkMismatchError(
                self.name,
                f"node is on chain {chain!r} but the server is configured for "
                f"{self.network.value!r}. Fix MCP_BITCOIN_NETWORK or point "
                "MCP_BITCOIN_CORE_URL at the right node.",
            )
        self._chain_verified = True

    # -- chain -------------------------------------------------------------

    async def ping(self) -> bool:
        info = await self._rpc("getblockchaininfo")
        await self._verify_chain(info)
        return True

    async def get_tip_height(self) -> int:
        info = await self._rpc("getblockchaininfo")
        await self._verify_chain(info)
        return int(info["blocks"])

    async def get_block_height(self) -> BlockHeader:
        info = await self._rpc("getblockchaininfo")
        await self._verify_chain(info)
        return await self.get_block(info["bestblockhash"])

    async def get_block(self, block_ref: str) -> BlockHeader:
        ref = block_ref.strip()
        if ref.isdigit():
            ref = await self._rpc("getblockhash", [int(ref)])
        data = await self._rpc("getblockheader", [ref, True])
        return BlockHeader(
            hash=data["hash"],
            height=data["height"],
            timestamp=data["time"],
            version=data.get("version"),
            nonce=data.get("nonce"),
            bits=data.get("bits"),
            difficulty=data.get("difficulty"),
            tx_count=data.get("nTx"),
            merkle_root=data.get("merkleroot"),
            prev_hash=data.get("previousblockhash"),
            confirmations=data.get("confirmations"),
            source=self.name,
        )

    # -- transactions ------------------------------------------------------

    async def get_transaction(self, txid: str) -> DecodedTransaction:
        txid = validate_txid(txid)
        data = await self._rpc("getrawtransaction", [txid, True])

        inputs = [
            TxInput(
                txid=vin.get("txid"),
                vout=vin.get("vout"),
                is_coinbase="coinbase" in vin,
                sequence=vin.get("sequence"),
            )
            for vin in data.get("vin", [])
        ]
        outputs = []
        for vout in data.get("vout", []):
            spk = vout.get("scriptPubKey", {})
            address = spk.get("address")
            if not address and spk.get("addresses"):
                address = spk["addresses"][0]
            value = btc_to_sats(vout["value"])
            outputs.append(
                TxOutput(
                    n=vout["n"],
                    address=address,
                    value_sats=value,
                    value_btc=sats_to_btc(value),
                    script_type=spk.get("type"),
                    script_hex=spk.get("hex"),
                )
            )

        return DecodedTransaction(
            txid=data["txid"],
            version=data.get("version", 0),
            locktime=data.get("locktime", 0),
            size=data.get("size"),
            vsize=data.get("vsize"),
            weight=data.get("weight"),
            is_segwit=data.get("hash") != data.get("txid"),
            inputs=inputs,
            outputs=outputs,
            total_output_sats=sum(o.value_sats for o in outputs),
            confirmations=data.get("confirmations"),
            block_hash=data.get("blockhash"),
            block_time=data.get("blocktime"),
            source=self.name,
        )

    async def get_raw_transaction(self, txid: str) -> str:
        raw: str = await self._rpc("getrawtransaction", [validate_txid(txid), False])
        return raw

    # -- fees and mempool --------------------------------------------------

    async def _smart_fee(self, target_blocks: int) -> float:
        result = await self._rpc("estimatesmartfee", [target_blocks])
        if result.get("errors"):
            raise ProviderError(
                self.name,
                f"fee estimation unavailable for {target_blocks} blocks: "
                f"{'; '.join(result['errors'])}",
            )
        feerate = result.get("feerate")
        if feerate is None:
            raise ProviderError(
                self.name,
                f"node returned no fee estimate for {target_blocks} blocks "
                "(it may still be syncing or have too little mempool history)",
            )
        # Core reports BTC per kvB; convert to sat/vB.
        return float(feerate) * 1e8 / 1000

    async def estimate_fee(self, target_blocks: int) -> FeeEstimate:
        rate = await self._smart_fee(target_blocks)
        return FeeEstimate(
            target_blocks=target_blocks,
            sat_per_vbyte=round(rate, 2),
            estimated_fee_p2wpkh_1in_1out_sats=round(rate * VSIZE_1IN_1OUT_P2WPKH),
            estimated_fee_p2wpkh_2in_2out_sats=round(rate * VSIZE_2IN_2OUT_P2WPKH),
            source=self.name,
        )

    async def get_recommended_fees(self) -> FeeTiers:
        """Derive tiers from three smart-fee targets.

        Deliberately propagates failure rather than substituting a floor value:
        a fabricated 1 sat/vB during a fee spike is worse than no answer.
        """
        priority = await self._smart_fee(1)
        normal = await self._smart_fee(6)
        economy = await self._smart_fee(144)
        return FeeTiers(
            economy=round(economy, 2),
            normal=round(normal, 2),
            priority=round(priority, 2),
            source=self.name,
        )

    async def get_mempool_info(self) -> MempoolInfo:
        data = await self._rpc("getmempoolinfo")
        min_fee = data.get("mempoolminfee")
        return MempoolInfo(
            tx_count=data.get("size", 0),
            vsize=data.get("bytes", 0),
            total_fee_sats=(btc_to_sats(data["total_fee"]) if data.get("total_fee") else None),
            memory_usage_bytes=data.get("usage"),
            max_mempool_bytes=data.get("maxmempool"),
            min_fee_rate_sat_vb=(round(float(min_fee) * 1e8 / 1000, 4) if min_fee else None),
            source=self.name,
        )

    async def get_mempool_entry(self, txid: str) -> dict[str, Any]:
        txid = validate_txid(txid)
        data = await self._rpc("getmempoolentry", [txid])
        fees = data.get("fees", {})
        vsize = data.get("vsize")
        base_fee = btc_to_sats(fees["base"]) if fees.get("base") else None
        return {
            "txid": txid,
            "fee_sats": base_fee,
            "vsize": vsize,
            "weight": data.get("weight"),
            "fee_rate_sat_vb": (round(base_fee / vsize, 2) if base_fee and vsize else None),
            "time": data.get("time"),
            "ancestor_count": data.get("ancestorcount"),
            "descendant_count": data.get("descendantcount"),
            "bip125_replaceable": data.get("bip125-replaceable"),
            "in_mempool": True,
            "source": self.name,
        }

    async def get_fee_histogram(self) -> list[dict[str, Any]]:
        raise NotImplementedError("Bitcoin Core does not expose a mempool fee histogram")

    # -- address queries: unsupported without an index ---------------------

    async def get_balance(self, address: str) -> Any:
        raise NotImplementedError(
            "Bitcoin Core has no address index; use an Electrum, mempool.space "
            "or Esplora backend for address queries"
        )

    async def get_utxos(self, address: str) -> Any:
        raise NotImplementedError(
            "Bitcoin Core has no address index; use an Electrum, mempool.space "
            "or Esplora backend for address queries"
        )

    async def get_tx_history(
        self, address: str, limit: int = 25, after_txid: str | None = None
    ) -> Any:
        raise NotImplementedError(
            "Bitcoin Core has no address index; use an Electrum, mempool.space "
            "or Esplora backend for address queries"
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
