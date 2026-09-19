"""Configuration loading and network parameters."""

from __future__ import annotations

import os

import pytest

from mcp_bitcoin.config import Network, ProviderType, ServerConfig, load_config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate each test from the ambient MCP_BITCOIN_* environment."""
    for key in list(os.environ):
        if key.startswith("MCP_BITCOIN_"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


class TestDefaults:
    def test_safe_defaults(self) -> None:
        config = ServerConfig()
        assert config.network is Network.MAINNET
        assert config.tor_enabled is False
        assert config.allow_mnemonic is False, "secrets must be opt-in"
        assert config.electrum_allow_self_signed is False, "TLS verified by default"


class TestNetworkParameters:
    @pytest.mark.parametrize(
        "network,coin_type,embit_key,hrp",
        [
            (Network.MAINNET, 0, "main", "bc"),
            (Network.TESTNET, 1, "test", "tb"),
            (Network.SIGNET, 1, "signet", "tb"),
            (Network.REGTEST, 1, "regtest", "bcrt"),
        ],
    )
    def test_parameters(self, network, coin_type, embit_key, hrp) -> None:
        assert network.coin_type == coin_type
        assert network.embit_key == embit_key
        assert network.embit_network["bech32"] == hrp

    def test_regtest_is_distinct_from_testnet(self) -> None:
        """These were collapsed together, which produced tb1 regtest addresses."""
        assert Network.REGTEST.embit_network["bech32"] != Network.TESTNET.embit_network["bech32"]

    @pytest.mark.parametrize(
        "network,port",
        [
            (Network.MAINNET, 8332),
            (Network.TESTNET, 18332),
            (Network.SIGNET, 38332),
            (Network.REGTEST, 18443),
        ],
    )
    def test_default_rpc_ports(self, network, port) -> None:
        assert network.core_rpc_port == port


class TestTorNormalization:
    @pytest.mark.parametrize(
        "given,expected",
        [
            ("socks5h://127.0.0.1:9050", "socks5://127.0.0.1:9050"),
            ("socks4a://127.0.0.1:9050", "socks4://127.0.0.1:9050"),
            ("socks5://127.0.0.1:9050", "socks5://127.0.0.1:9050"),
        ],
    )
    def test_normalizes_scheme(self, given, expected) -> None:
        """python-socks rejects socks5h outright, which broke all Tor usage.

        Every piece of Tor documentation shows socks5h, so accepting it and
        rewriting is the only behavior that matches user expectation.
        """
        assert ServerConfig(tor_proxy=given).tor_proxy == expected

    def test_empty_disables_tor(self) -> None:
        assert ServerConfig(tor_proxy="").tor_enabled is False
        assert ServerConfig(tor_proxy=None).tor_enabled is False

    def test_set_enables_tor(self) -> None:
        assert ServerConfig(tor_proxy="socks5://127.0.0.1:9050").tor_enabled is True


class TestProviderSelection:
    def test_parses_priority_string(self) -> None:
        config = ServerConfig(provider_priority="mempool, esplora")
        assert config.provider_priority == [ProviderType.MEMPOOL, ProviderType.ESPLORA]

    def test_only_configured_backends_are_used(self) -> None:
        config = ServerConfig(
            mempool_url="https://mempool.space/api",
            provider_priority=[
                ProviderType.CORE,
                ProviderType.MEMPOOL,
                ProviderType.ESPLORA,
            ],
        )
        assert config.configured_providers() == [ProviderType.MEMPOOL]

    def test_priority_order_is_respected(self) -> None:
        config = ServerConfig(
            mempool_url="https://mempool.space/api",
            esplora_url="https://blockstream.info/api",
            provider_priority=[ProviderType.ESPLORA, ProviderType.MEMPOOL],
        )
        assert config.configured_providers() == [
            ProviderType.ESPLORA,
            ProviderType.MEMPOOL,
        ]

    def test_none_configured(self) -> None:
        assert ServerConfig().configured_providers() == []


class TestLoadConfig:
    def test_reads_environment(self, clean_env) -> None:
        clean_env.setenv("MCP_BITCOIN_NETWORK", "testnet")
        clean_env.setenv("MCP_BITCOIN_MEMPOOL_URL", "https://mempool.space/testnet/api")
        clean_env.setenv("MCP_BITCOIN_TIMEOUT", "15")
        clean_env.setenv("MCP_BITCOIN_ALLOW_MNEMONIC", "true")

        config = load_config()
        assert config.network is Network.TESTNET
        assert config.timeout == 15
        assert config.allow_mnemonic is True

    def test_empty_strings_fall_back_to_defaults(self, clean_env) -> None:
        """An empty value in a JSON config must not crash startup."""
        clean_env.setenv("MCP_BITCOIN_NETWORK", "")
        clean_env.setenv("MCP_BITCOIN_TIMEOUT", "")
        config = load_config()
        assert config.network is Network.MAINNET
        assert config.timeout == 30

    def test_defaults_when_unset(self, clean_env) -> None:
        config = load_config()
        assert config.network is Network.MAINNET
        assert config.allow_mnemonic is False

    def test_rejects_unknown_network(self, clean_env) -> None:
        from pydantic import ValidationError

        clean_env.setenv("MCP_BITCOIN_NETWORK", "dogecoin")
        with pytest.raises(ValidationError):
            load_config()

    def test_tor_normalized_from_env(self, clean_env) -> None:
        clean_env.setenv("MCP_BITCOIN_TOR_PROXY", "socks5h://127.0.0.1:9050")
        assert load_config().tor_proxy == "socks5://127.0.0.1:9050"
