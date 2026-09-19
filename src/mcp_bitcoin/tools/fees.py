"""Fee estimation and mempool tool handlers."""

from __future__ import annotations

from typing import Any

from mcp_bitcoin.config import Network
from mcp_bitcoin.providers.base import ProviderManager
from mcp_bitcoin.types import FeeEstimate, FeeTiers, MempoolInfo
from mcp_bitcoin.utils.validation import validate_txid

MAX_TARGET_BLOCKS = 1008  # one week


async def handle_estimate_fee(
    pm: ProviderManager, target_blocks: int = 6, network: Network = Network.MAINNET
) -> dict[str, Any]:
    if not 1 <= target_blocks <= MAX_TARGET_BLOCKS:
        raise ValueError(
            f"target_blocks must be between 1 and {MAX_TARGET_BLOCKS}, got {target_blocks}"
        )
    estimate: FeeEstimate = await pm.call("estimate_fee", target_blocks)
    return estimate.model_dump(exclude_none=True)


async def handle_get_recommended_fees(
    pm: ProviderManager, network: Network = Network.MAINNET
) -> dict[str, Any]:
    tiers: FeeTiers = await pm.call("get_recommended_fees")
    return tiers.model_dump(exclude_none=True)


async def handle_get_fee_histogram(
    pm: ProviderManager, network: Network = Network.MAINNET
) -> dict[str, Any]:
    histogram = await pm.call("get_fee_histogram")
    return {"network": network.value, "entries": len(histogram), "histogram": histogram}


async def handle_get_mempool_info(
    pm: ProviderManager, network: Network = Network.MAINNET
) -> dict[str, Any]:
    info: MempoolInfo = await pm.call("get_mempool_info")
    return info.model_dump(exclude_none=True)


async def handle_get_mempool_entry(
    pm: ProviderManager, txid: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    entry: dict[str, Any] = await pm.call("get_mempool_entry", validate_txid(txid))
    return entry
