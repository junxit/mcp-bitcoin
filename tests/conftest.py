"""Shared fixtures."""

from __future__ import annotations

from typing import Any

import pytest

from mcp_bitcoin.config import Network, ProviderType, ServerConfig
from mcp_bitcoin.derivation.hd import master_key_from_seed
from mcp_bitcoin.derivation.mnemonic import mnemonic_to_seed
from mcp_bitcoin.providers.base import ProviderError, ProviderManager
from mcp_bitcoin.types import Balance, TxHistoryPage
from mcp_bitcoin.utils.units import sats_to_btc
from tests.vectors import ABANDON_MNEMONIC


@pytest.fixture
def master_key():
    """BIP-32 master key for the canonical `abandon ... about` phrase."""
    return master_key_from_seed(mnemonic_to_seed(ABANDON_MNEMONIC))


@pytest.fixture
def watch_only_config() -> ServerConfig:
    """Default posture: secrets refused."""
    return ServerConfig(allow_mnemonic=False)


@pytest.fixture
def permissive_config() -> ServerConfig:
    """Opt-in posture: secrets accepted."""
    return ServerConfig(allow_mnemonic=True)


class FakeProvider:
    """Configurable stand-in for a backend."""

    provider_type = ProviderType.MEMPOOL

    def __init__(
        self,
        name: str = "fake",
        *,
        fail: bool = False,
        unsupported: set[str] | None = None,
        network: Network = Network.MAINNET,
    ):
        self.name = name
        self.url = f"http://{name}.invalid"
        self.network = network
        self._fail = fail
        self._unsupported = unsupported or set()
        self.calls: list[str] = []

    def _guard(self, method: str) -> None:
        self.calls.append(method)
        if method in self._unsupported:
            raise NotImplementedError(f"{self.name} does not support {method}")
        if self._fail:
            raise ProviderError(self.name, "backend is down")

    async def ping(self) -> bool:
        self._guard("ping")
        return True

    async def get_balance(self, address: str) -> Balance:
        self._guard("get_balance")
        return Balance(
            address=address,
            confirmed_sats=100_000,
            unconfirmed_sats=0,
            total_sats=100_000,
            confirmed_btc=sats_to_btc(100_000),
            unconfirmed_btc=sats_to_btc(0),
            total_btc=sats_to_btc(100_000),
            source=self.name,
        )

    async def get_tx_history(
        self, address: str, limit: int = 25, after_txid: str | None = None
    ) -> TxHistoryPage:
        self._guard("get_tx_history")
        return TxHistoryPage(source=self.name)

    async def get_recommended_fees(self) -> Any:
        self._guard("get_recommended_fees")
        raise NotImplementedError

    async def close(self) -> None:
        pass


@pytest.fixture
def make_manager():
    """Build a ProviderManager from a list of FakeProviders."""

    def _make(*providers: FakeProvider, cooldown: int = 60, timeout: int = 5):
        pm = ProviderManager(ServerConfig(failover_cooldown=cooldown, timeout=timeout))
        for p in providers:
            pm.register(p)
        return pm

    return _make
