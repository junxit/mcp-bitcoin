"""Shared base for Esplora-family REST backends (Esplora and mempool.space).

mempool.space is a fork of Blockstream's Esplora and serves the same core REST
surface, plus a richer ``/v1/`` namespace. Implementing the common part once
means the confirmation, pagination and vsize fixes land in both backends.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
from aiohttp_socks import ProxyConnector

from mcp_bitcoin.config import Network, ProviderType
from mcp_bitcoin.providers.base import ProviderError
from mcp_bitcoin.types import (
    UTXO,
    Balance,
    BlockHeader,
    DecodedTransaction,
    FeeEstimate,
    FeeTiers,
    MempoolInfo,
    TxHistoryPage,
    TxInput,
    TxOutput,
    TxSummary,
)
from mcp_bitcoin.utils.units import sats_to_btc
from mcp_bitcoin.utils.validation import validate_address

logger = logging.getLogger(__name__)

# Typical transaction sizes for the fee illustrations, in vbytes.
VSIZE_1IN_1OUT_P2WPKH = 110
VSIZE_2IN_2OUT_P2WPKH = 208

# Esplora returns at most this many confirmed transactions per page.
ESPLORA_PAGE_SIZE = 25


def vsize_from_weight(weight: int | None, fallback: int | None = None) -> int | None:
    """Convert weight units to vbytes, rounding up as consensus does."""
    if not weight:
        return fallback
    return (weight + 3) // 4


class EsploraFamilyProvider:
    """Common implementation for Esplora-compatible REST APIs."""

    provider_type = ProviderType.ESPLORA
    name = "esplora"

    def __init__(self, url: str, network: Network, tor_proxy: str | None = None):
        self.url = url.rstrip("/")
        self.network = network
        self._tor_proxy = tor_proxy
        self._session: aiohttp.ClientSession | None = None

    # -- transport ---------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = ProxyConnector.from_url(self._tor_proxy) if self._tor_proxy else None
            self._session = aiohttp.ClientSession(connector=connector)
        return self._session

    async def _request(self, path: str, *, raw: bool = False) -> Any:
        session = await self._get_session()
        url = f"{self.url}{path}"
        try:
            async with session.get(url) as resp:
                body = await resp.text()
                if resp.status == 404:
                    raise ProviderError(self.name, f"not found: {path}")
                if resp.status != 200:
                    raise ProviderError(self.name, f"HTTP {resp.status} for {path}: {body[:200]}")
                if raw:
                    return body.strip()
                content_type = resp.headers.get("Content-Type", "")
                if "json" in content_type:
                    import json

                    return json.loads(body)
                return body.strip()
        except aiohttp.ClientError as exc:
            raise ProviderError(self.name, f"{type(exc).__name__}: {exc}") from exc

    async def _get_int(self, path: str) -> int:
        value = await self._request(path, raw=True)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ProviderError(self.name, f"expected a number from {path}, got {value!r}") from exc

    def _check_address(self, address: str) -> str:
        validate_address(address, self.network)
        return address.strip()

    # -- chain tip ---------------------------------------------------------

    async def ping(self) -> bool:
        await self._get_int("/blocks/tip/height")
        return True

    async def get_tip_height(self) -> int:
        return await self._get_int("/blocks/tip/height")

    async def get_block_height(self) -> BlockHeader:
        height = await self.get_tip_height()
        block_hash = await self._request(f"/block-height/{height}", raw=True)
        return await self.get_block(block_hash)

    async def get_block(self, block_ref: str) -> BlockHeader:
        ref = block_ref.strip()
        if ref.isdigit():
            ref = await self._request(f"/block-height/{ref}", raw=True)
        data = await self._request(f"/block/{ref}")
        tip = await self.get_tip_height()
        height = data["height"]
        return BlockHeader(
            hash=data["id"],
            height=height,
            timestamp=data["timestamp"],
            version=data.get("version"),
            nonce=data.get("nonce"),
            bits=hex(data["bits"]) if isinstance(data.get("bits"), int) else data.get("bits"),
            difficulty=data.get("difficulty"),
            tx_count=data.get("tx_count"),
            size=data.get("size"),
            weight=data.get("weight"),
            merkle_root=data.get("merkle_root"),
            prev_hash=data.get("previousblockhash"),
            confirmations=max(tip - height + 1, 0),
            source=self.name,
        )

    # -- address queries ---------------------------------------------------

    async def get_balance(self, address: str) -> Balance:
        address = self._check_address(address)
        data = await self._request(f"/address/{address}")
        chain = data.get("chain_stats", {})
        pool = data.get("mempool_stats", {})
        confirmed = chain.get("funded_txo_sum", 0) - chain.get("spent_txo_sum", 0)
        unconfirmed = pool.get("funded_txo_sum", 0) - pool.get("spent_txo_sum", 0)
        total = confirmed + unconfirmed
        return Balance(
            address=address,
            confirmed_sats=confirmed,
            unconfirmed_sats=unconfirmed,
            total_sats=total,
            confirmed_btc=sats_to_btc(confirmed),
            unconfirmed_btc=sats_to_btc(unconfirmed),
            total_btc=sats_to_btc(total),
            source=self.name,
        )

    async def get_utxos(self, address: str) -> list[UTXO]:
        address = self._check_address(address)
        data = await self._request(f"/address/{address}/utxo")
        tip = await self.get_tip_height()
        utxos = []
        for u in data:
            status = u.get("status", {})
            height = status.get("block_height")
            confirmations = max(tip - height + 1, 0) if status.get("confirmed") and height else 0
            utxos.append(
                UTXO(
                    txid=u["txid"],
                    vout=u["vout"],
                    value_sats=u["value"],
                    value_btc=sats_to_btc(u["value"]),
                    confirmations=confirmations,
                    block_height=height,
                )
            )
        return utxos

    async def get_tx_history(
        self, address: str, limit: int = 25, after_txid: str | None = None
    ) -> TxHistoryPage:
        """Fetch one page of history, newest first.

        Uses Esplora's cursor pagination rather than slicing a capped response,
        so deep history is actually reachable instead of returning an empty
        list that reads as "no more transactions".
        """
        address = self._check_address(address)
        path = f"/address/{address}/txs"
        if after_txid:
            path = f"/address/{address}/txs/chain/{after_txid}"

        data = await self._request(path)
        tip = await self.get_tip_height()

        transactions = []
        for tx in data[:limit]:
            status = tx.get("status", {})
            height = status.get("block_height")
            transactions.append(
                TxSummary(
                    txid=tx["txid"],
                    block_height=height,
                    confirmations=max(tip - height + 1, 0) if height else 0,
                    fee_sats=tx.get("fee"),
                    size=tx.get("size"),
                    vsize=vsize_from_weight(tx.get("weight"), tx.get("size")),
                    timestamp=status.get("block_time"),
                )
            )

        # A full upstream page means more may exist; a short one means we're done.
        has_more = len(data) >= ESPLORA_PAGE_SIZE and len(transactions) == limit
        return TxHistoryPage(
            transactions=transactions,
            has_more=has_more,
            next_cursor=transactions[-1].txid if has_more and transactions else None,
            source=self.name,
        )

    # -- transactions ------------------------------------------------------

    def _decode_tx(self, data: dict[str, Any], tip: int | None) -> DecodedTransaction:
        inputs = [
            TxInput(
                txid=vin.get("txid"),
                vout=vin.get("vout"),
                address=(vin.get("prevout") or {}).get("scriptpubkey_address"),
                value_sats=(vin.get("prevout") or {}).get("value"),
                value_btc=(
                    sats_to_btc((vin["prevout"] or {})["value"])
                    if (vin.get("prevout") or {}).get("value") is not None
                    else None
                ),
                is_coinbase=bool(vin.get("is_coinbase")),
                sequence=vin.get("sequence"),
            )
            for vin in data.get("vin", [])
        ]
        outputs = [
            TxOutput(
                n=i,
                address=vout.get("scriptpubkey_address"),
                value_sats=vout.get("value", 0),
                value_btc=sats_to_btc(vout.get("value", 0)),
                script_type=vout.get("scriptpubkey_type"),
                script_hex=vout.get("scriptpubkey"),
            )
            for i, vout in enumerate(data.get("vout", []))
        ]

        status = data.get("status", {})
        height = status.get("block_height")
        weight = data.get("weight")
        vsize = vsize_from_weight(weight, data.get("size"))
        fee = data.get("fee")

        return DecodedTransaction(
            txid=data["txid"],
            version=data.get("version", 0),
            locktime=data.get("locktime", 0),
            size=data.get("size"),
            vsize=vsize,
            weight=weight,
            fee_sats=fee,
            fee_rate_sat_vb=round(fee / vsize, 2) if fee and vsize else None,
            is_segwit=any(vin.get("witness") for vin in data.get("vin", [])),
            inputs=inputs,
            outputs=outputs,
            total_output_sats=sum(o.value_sats for o in outputs),
            confirmations=(max(tip - height + 1, 0) if tip is not None and height else 0),
            block_hash=status.get("block_hash"),
            block_height=height,
            block_time=status.get("block_time"),
            source=self.name,
        )

    async def get_transaction(self, txid: str) -> DecodedTransaction:
        data = await self._request(f"/tx/{txid}")
        tip = None
        if data.get("status", {}).get("block_height"):
            tip = await self.get_tip_height()
        return self._decode_tx(data, tip)

    async def get_raw_transaction(self, txid: str) -> str:
        raw: str = await self._request(f"/tx/{txid}/hex", raw=True)
        return raw

    # -- fees and mempool --------------------------------------------------

    async def _fee_estimates(self) -> dict[str, float]:
        data = await self._request("/fee-estimates")
        if not isinstance(data, dict) or not data:
            raise ProviderError(self.name, "fee estimates unavailable")
        return {str(k): float(v) for k, v in data.items()}

    async def estimate_fee(self, target_blocks: int) -> FeeEstimate:
        estimates = await self._fee_estimates()
        targets = sorted(int(k) for k in estimates)
        # Cheapest estimate that still confirms within the requested window.
        eligible = [t for t in targets if t <= target_blocks]
        chosen = max(eligible) if eligible else min(targets)
        rate = estimates[str(chosen)]
        return FeeEstimate(
            target_blocks=target_blocks,
            sat_per_vbyte=round(rate, 2),
            estimated_fee_p2wpkh_1in_1out_sats=round(rate * VSIZE_1IN_1OUT_P2WPKH),
            estimated_fee_p2wpkh_2in_2out_sats=round(rate * VSIZE_2IN_2OUT_P2WPKH),
            source=self.name,
        )

    async def get_recommended_fees(self) -> FeeTiers:
        estimates = await self._fee_estimates()

        def at(target: int) -> float | None:
            return estimates.get(str(target))

        priority = at(1) or at(2)
        normal = at(6) or at(3) or priority
        economy = at(144) or at(72) or at(25) or normal
        if priority is None or normal is None or economy is None:
            raise ProviderError(self.name, "fee estimates incomplete")
        return FeeTiers(
            economy=round(economy, 2),
            normal=round(normal, 2),
            priority=round(priority, 2),
            source=self.name,
        )

    async def get_mempool_info(self) -> MempoolInfo:
        data = await self._request("/mempool")
        return MempoolInfo(
            tx_count=data.get("count", 0),
            vsize=data.get("vsize", 0),
            total_fee_sats=data.get("total_fee"),
            source=self.name,
        )

    async def get_fee_histogram(self) -> list[dict[str, Any]]:
        data = await self._request("/mempool")
        histogram = data.get("fee_histogram") or []
        return [{"fee_rate_sat_vb": float(rate), "vsize": int(vsize)} for rate, vsize in histogram]

    async def get_mempool_entry(self, txid: str) -> dict[str, Any]:
        data = await self._request(f"/tx/{txid}")
        status = data.get("status", {})
        if status.get("confirmed"):
            raise ProviderError(
                self.name,
                f"transaction {txid} is confirmed in block "
                f"{status.get('block_height')}, not in the mempool",
            )
        weight = data.get("weight")
        vsize = vsize_from_weight(weight, data.get("size"))
        fee = data.get("fee")
        return {
            "txid": txid,
            "fee_sats": fee,
            "size": data.get("size"),
            "vsize": vsize,
            "weight": weight,
            "fee_rate_sat_vb": round(fee / vsize, 2) if fee and vsize else None,
            "in_mempool": True,
            "source": self.name,
        }

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class EsploraProvider(EsploraFamilyProvider):
    """Blockstream Esplora REST backend."""

    provider_type = ProviderType.ESPLORA
    name = "esplora"


class MempoolProvider(EsploraFamilyProvider):
    """mempool.space REST backend.

    Overrides fee and mempool endpoints with mempool.space's richer ``/v1/``
    namespace, which gives real recommended tiers and projected-block data.
    """

    provider_type = ProviderType.MEMPOOL
    name = "mempool"

    async def get_recommended_fees(self) -> FeeTiers:
        fees = await self._request("/v1/fees/recommended")
        return FeeTiers(
            economy=float(fees.get("economyFee", fees.get("minimumFee", 1))),
            normal=float(fees.get("hourFee", 1)),
            priority=float(fees.get("fastestFee", 1)),
            source=self.name,
        )

    async def estimate_fee(self, target_blocks: int) -> FeeEstimate:
        fees = await self._request("/v1/fees/recommended")
        if target_blocks <= 1:
            rate = fees.get("fastestFee")
        elif target_blocks <= 3:
            rate = fees.get("halfHourFee")
        elif target_blocks <= 6:
            rate = fees.get("hourFee")
        elif target_blocks <= 144:
            rate = fees.get("economyFee")
        else:
            rate = fees.get("minimumFee")
        if rate is None:
            raise ProviderError(self.name, "fee estimates unavailable")
        rate = float(rate)
        return FeeEstimate(
            target_blocks=target_blocks,
            sat_per_vbyte=rate,
            estimated_fee_p2wpkh_1in_1out_sats=round(rate * VSIZE_1IN_1OUT_P2WPKH),
            estimated_fee_p2wpkh_2in_2out_sats=round(rate * VSIZE_2IN_2OUT_P2WPKH),
            source=self.name,
        )

    async def get_fee_histogram(self) -> list[dict[str, Any]]:
        """Projected next blocks, which is more useful than a raw histogram."""
        blocks = await self._request("/v1/fees/mempool-blocks")
        return [
            {
                "block": i,
                "median_fee_sat_vb": round(float(b.get("medianFee", 0)), 2),
                "fee_range_sat_vb": [round(float(f), 2) for f in b.get("feeRange", [])],
                "tx_count": b.get("nTx"),
                "vsize": b.get("blockVSize"),
            }
            for i, b in enumerate(blocks)
        ]
