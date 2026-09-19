"""Utility tool handlers."""

from __future__ import annotations

from typing import Any

from mcp_bitcoin.config import ServerConfig
from mcp_bitcoin.providers.base import ProviderManager
from mcp_bitcoin.utils.units import convert_units as _convert


def _offline_notice(config: ServerConfig) -> str | None:
    if config.configured_providers():
        return None
    return (
        "No backend is configured, so only offline tools work (derivation, "
        "address validation, unit conversion, decoding). Set one of "
        "MCP_BITCOIN_MEMPOOL_URL, MCP_BITCOIN_ESPLORA_URL, "
        "MCP_BITCOIN_ELECTRUM_URL or MCP_BITCOIN_CORE_URL to enable chain queries."
    )


async def handle_ping(pm: ProviderManager, config: ServerConfig) -> dict[str, Any]:
    """Report server health and backend reachability."""
    statuses = await pm.ping_all(probe_only=True)
    reachable = sum(1 for s in statuses if s.reachable)

    result: dict[str, Any] = {
        "status": "ok" if reachable or not statuses else "degraded",
        "network": config.network.value,
        "tor_enabled": config.tor_enabled,
        "watch_only": not config.allow_mnemonic,
        "backends_reachable": f"{reachable}/{len(statuses)}",
        "backends": [s.model_dump(exclude_none=True) for s in statuses],
    }
    notice = _offline_notice(config)
    if notice:
        result["notice"] = notice
    return result


async def handle_list_backends(pm: ProviderManager, config: ServerConfig) -> dict[str, Any]:
    """List configured backends, their status and their capabilities."""
    statuses = await pm.ping_all(probe_only=True)
    result: dict[str, Any] = {
        "network": config.network.value,
        "tor_enabled": config.tor_enabled,
        "priority": [p.value for p in config.provider_priority],
        "configured": [p.value for p in config.configured_providers()],
        "backends": [s.model_dump(exclude_none=True) for s in statuses],
        "capabilities": {
            "address_queries": "electrum, mempool, esplora (not Bitcoin Core)",
            "block_data": "core, mempool, esplora (not Electrum)",
            "mempool_details": "core, mempool (not Electrum or Esplora)",
            "fee_tiers": "core, mempool, esplora (not Electrum)",
        },
    }
    notice = _offline_notice(config)
    if notice:
        result["notice"] = notice
    return result


async def handle_convert_units(amount: str, from_unit: str, to_unit: str) -> dict[str, Any]:
    """Convert between BTC, mBTC, bits and satoshis."""
    converted = _convert(amount, from_unit, to_unit)
    return {
        "input": {"amount": str(amount), "unit": from_unit.lower()},
        "output": {"amount": converted, "unit": to_unit.lower()},
    }
