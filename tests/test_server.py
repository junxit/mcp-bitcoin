"""Server wiring: tool registration, error translation and startup checks."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network, ServerConfig
from mcp_bitcoin.providers.base import BackendUnavailableError, ProviderManager
from mcp_bitcoin.server import (
    _network,
    build_provider_manager,
    check_tor_proxy,
    configure,
    mcp,
    tool_handler,
)
from mcp_bitcoin.tools.derivation import SecretsNotAllowedError

EXPECTED_TOOLS = {
    # utility
    "ping",
    "list_backends",
    "convert_units",
    # derivation
    "generate_mnemonic",
    "validate_mnemonic",
    "derive_addresses",
    "derive_from_path",
    "derive_xpub",
    "derive_entropy_bip85",
    "decode_wif",
    "get_address_from_pubkey",
    # chain queries
    "get_balance",
    "get_utxos",
    "get_tx_history",
    "get_transaction",
    "get_raw_transaction",
    "decode_raw_transaction",
    "get_block",
    "get_block_height",
    "validate_address",
    "decode_script",
    "decode_psbt",
    # fees
    "estimate_fee",
    "get_recommended_fees",
    "get_fee_histogram",
    "get_mempool_info",
    "get_mempool_entry",
}

# Cut in v0.1: the signing path was non-functional and rebuilding it correctly
# requires PSBT support. See ROADMAP.md.
REMOVED_TOOLS = {
    "create_transaction",
    "create_transaction_from_address",
    "sign_transaction",
    "broadcast_transaction",
}


class TestToolRegistration:
    def test_expected_tools_present(self) -> None:
        assert set(mcp._tool_manager._tools) == EXPECTED_TOOLS

    def test_count(self) -> None:
        assert len(mcp._tool_manager._tools) == 27

    def test_money_moving_tools_absent(self) -> None:
        assert not REMOVED_TOOLS & set(mcp._tool_manager._tools)

    def test_every_tool_documented(self) -> None:
        for name, tool in mcp._tool_manager._tools.items():
            assert tool.description, f"{name} has no description"

    def test_secret_tools_warn_in_description(self) -> None:
        """The model reads these; the permission requirement must be visible."""
        for name in (
            "generate_mnemonic",
            "derive_xpub",
            "derive_entropy_bip85",
            "decode_wif",
        ):
            description = mcp._tool_manager._tools[name].description
            assert "MCP_BITCOIN_ALLOW_MNEMONIC" in description


class TestNetworkOverride:
    def test_defaults_to_config(self) -> None:
        configure(ServerConfig(network=Network.TESTNET), ProviderManager(ServerConfig()))
        assert _network(None) is Network.TESTNET

    def test_explicit_override(self) -> None:
        configure(ServerConfig(), ProviderManager(ServerConfig()))
        assert _network("regtest") is Network.REGTEST

    def test_case_insensitive(self) -> None:
        configure(ServerConfig(), ProviderManager(ServerConfig()))
        assert _network("  MAINNET ") is Network.MAINNET

    def test_rejects_unknown(self) -> None:
        configure(ServerConfig(), ProviderManager(ServerConfig()))
        with pytest.raises(ValueError, match="Unknown network"):
            _network("dogecoin")


class TestErrorTranslation:
    """Internal exceptions must become explanations, not tracebacks."""

    async def test_secrets_refusal(self) -> None:
        @tool_handler
        async def failing() -> str:
            raise SecretsNotAllowedError()

        out = await failing()
        assert "watch-only" in out
        assert "MCP_BITCOIN_ALLOW_MNEMONIC" in out

    async def test_backend_unavailable(self) -> None:
        @tool_handler
        async def failing() -> str:
            raise BackendUnavailableError("get_balance", configured=0)

        out = await failing()
        assert "backend_unavailable" in out
        assert "No backend could answer" in out

    async def test_value_error(self) -> None:
        @tool_handler
        async def failing() -> str:
            raise ValueError("that address is for mainnet")

        out = await failing()
        assert "invalid_input" in out
        assert "mainnet" in out

    async def test_unexpected_error_is_contained(self) -> None:
        @tool_handler
        async def failing() -> str:
            raise RuntimeError("boom")

        out = await failing()
        assert "internal_error" in out
        assert "boom" in out

    async def test_success_passes_through(self) -> None:
        @tool_handler
        async def fine() -> str:
            return "all good"

        assert await fine() == "all good"


class TestProviderConstruction:
    def test_no_backends_configured(self) -> None:
        assert build_provider_manager(ServerConfig()).provider_count == 0

    def test_registers_configured_backends(self) -> None:
        pm = build_provider_manager(
            ServerConfig(
                mempool_url="https://mempool.space/api",
                esplora_url="https://blockstream.info/api",
            )
        )
        assert pm.provider_count == 2

    def test_bad_backend_url_does_not_abort_startup(self) -> None:
        """One malformed URL must not take down the working backends."""
        pm = build_provider_manager(
            ServerConfig(
                electrum_url="no-scheme-here:50002",
                mempool_url="https://mempool.space/api",
            )
        )
        assert pm.provider_count == 1

    def test_respects_priority_order(self) -> None:
        from mcp_bitcoin.config import ProviderType

        pm = build_provider_manager(
            ServerConfig(
                mempool_url="https://mempool.space/api",
                esplora_url="https://blockstream.info/api",
                provider_priority=[ProviderType.ESPLORA, ProviderType.MEMPOOL],
            )
        )
        assert [s.provider.name for s in pm._states] == ["esplora", "mempool"]


class TestTorPreflight:
    def test_exits_when_proxy_unreachable(self) -> None:
        """Tor is advertised as fail-closed, so startup must abort."""
        with pytest.raises(SystemExit) as exc:
            check_tor_proxy("socks5://127.0.0.1:1", timeout=0.5)
        assert exc.value.code == 1

    def test_exits_on_malformed_proxy_url(self) -> None:
        with pytest.raises(SystemExit):
            check_tor_proxy("not-a-url")
