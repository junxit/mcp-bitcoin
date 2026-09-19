"""Configuration loading from environment variables."""

from __future__ import annotations

import os
from enum import StrEnum
from typing import Any

from embit.networks import NETWORKS
from pydantic import BaseModel, field_validator


class Network(StrEnum):
    MAINNET = "mainnet"
    TESTNET = "testnet"
    SIGNET = "signet"
    REGTEST = "regtest"

    @property
    def is_mainnet(self) -> bool:
        return self == Network.MAINNET

    @property
    def coin_type(self) -> int:
        """BIP-44 coin type: 0 for mainnet, 1 for every test network."""
        return 0 if self.is_mainnet else 1

    @property
    def embit_key(self) -> str:
        """Key into embit's NETWORKS table."""
        return {
            Network.MAINNET: "main",
            Network.TESTNET: "test",
            Network.SIGNET: "signet",
            Network.REGTEST: "regtest",
        }[self]

    @property
    def embit_network(self) -> dict[str, Any]:
        """embit network parameters for this network.

        Using this instead of a two-way ``is_mainnet`` branch is what makes
        regtest produce ``bcrt1...`` addresses rather than ``tb1...``.
        """
        params: dict[str, Any] = NETWORKS[self.embit_key]
        return params

    @property
    def address_prefixes(self) -> tuple[str, ...]:
        """Valid bech32 HRP and base58 first characters for this network."""
        return {
            Network.MAINNET: ("bc1", "1", "3"),
            Network.TESTNET: ("tb1", "m", "n", "2"),
            Network.SIGNET: ("tb1", "m", "n", "2"),
            Network.REGTEST: ("bcrt1", "m", "n", "2"),
        }[self]

    @property
    def core_rpc_port(self) -> int:
        """Default Bitcoin Core RPC port for this network."""
        return {
            Network.MAINNET: 8332,
            Network.TESTNET: 18332,
            Network.SIGNET: 38332,
            Network.REGTEST: 18443,
        }[self]


class ProviderType(StrEnum):
    CORE = "core"
    ELECTRUM = "electrum"
    MEMPOOL = "mempool"
    ESPLORA = "esplora"


class ServerConfig(BaseModel):
    """Server configuration loaded from environment variables."""

    network: Network = Network.MAINNET
    tor_proxy: str | None = None
    core_url: str | None = None
    core_cookie_path: str | None = None
    electrum_url: str | None = None
    electrum_allow_self_signed: bool = False
    mempool_url: str | None = None
    esplora_url: str | None = None
    provider_priority: list[ProviderType] = [
        ProviderType.CORE,
        ProviderType.ELECTRUM,
        ProviderType.MEMPOOL,
        ProviderType.ESPLORA,
    ]
    allow_mnemonic: bool = False
    failover_cooldown: int = 60
    timeout: int = 30

    @field_validator("provider_priority", mode="before")
    @classmethod
    def parse_provider_priority(cls, v: object) -> object:
        if isinstance(v, str):
            return [ProviderType(p.strip()) for p in v.split(",") if p.strip()]
        return v

    @field_validator("tor_proxy", mode="before")
    @classmethod
    def normalize_tor_proxy(cls, v: object) -> object:
        """Normalize ``socks5h://`` to ``socks5://``.

        ``socks5h`` is curl's spelling for "resolve DNS through the proxy" and is
        what most Tor documentation shows, but python-socks rejects it outright.
        python-socks already defaults to remote DNS for SOCKS5, so rewriting the
        scheme preserves the intended behavior instead of failing every request.
        """
        if isinstance(v, str) and v:
            if v.startswith("socks5h://"):
                return "socks5://" + v[len("socks5h://") :]
            if v.startswith("socks4a://"):
                return "socks4://" + v[len("socks4a://") :]
        return v

    @property
    def tor_enabled(self) -> bool:
        return bool(self.tor_proxy)

    def configured_providers(self) -> list[ProviderType]:
        """Return only providers that have a URL configured, in priority order."""
        url_map = {
            ProviderType.CORE: self.core_url,
            ProviderType.ELECTRUM: self.electrum_url,
            ProviderType.MEMPOOL: self.mempool_url,
            ProviderType.ESPLORA: self.esplora_url,
        }
        return [p for p in self.provider_priority if url_map.get(p)]


def load_config() -> ServerConfig:
    """Load configuration from ``MCP_BITCOIN_*`` environment variables."""
    env_map = {
        "network": os.environ.get("MCP_BITCOIN_NETWORK") or None,
        "tor_proxy": os.environ.get("MCP_BITCOIN_TOR_PROXY") or None,
        "core_url": os.environ.get("MCP_BITCOIN_CORE_URL") or None,
        "core_cookie_path": os.environ.get("MCP_BITCOIN_CORE_COOKIE_PATH") or None,
        "electrum_url": os.environ.get("MCP_BITCOIN_ELECTRUM_URL") or None,
        "electrum_allow_self_signed": (
            os.environ.get("MCP_BITCOIN_ELECTRUM_ALLOW_SELF_SIGNED") or None
        ),
        "mempool_url": os.environ.get("MCP_BITCOIN_MEMPOOL_URL") or None,
        "esplora_url": os.environ.get("MCP_BITCOIN_ESPLORA_URL") or None,
        "provider_priority": os.environ.get("MCP_BITCOIN_PROVIDER_PRIORITY") or None,
        "allow_mnemonic": os.environ.get("MCP_BITCOIN_ALLOW_MNEMONIC") or None,
        "failover_cooldown": os.environ.get("MCP_BITCOIN_FAILOVER_COOLDOWN") or None,
        "timeout": os.environ.get("MCP_BITCOIN_TIMEOUT") or None,
    }
    filtered: dict[str, Any] = {k: v for k, v in env_map.items() if v is not None}
    return ServerConfig(**filtered)
