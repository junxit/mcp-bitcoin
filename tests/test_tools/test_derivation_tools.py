"""Derivation tool handlers, including the watch-only gate."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.tools.derivation import (
    SecretsNotAllowedError,
    handle_decode_wif,
    handle_derive_addresses,
    handle_derive_entropy_bip85,
    handle_derive_from_path,
    handle_derive_xpub,
    handle_generate_mnemonic,
    handle_get_address_from_pubkey,
    handle_validate_mnemonic,
    looks_like_xpub,
)
from tests.vectors import ABANDON_MNEMONIC, BIP84_MAINNET


class TestWatchOnlyGate:
    """Secrets are refused unless the operator opted in."""

    async def test_generate_refused(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError):
            await handle_generate_mnemonic(watch_only_config)

    async def test_mnemonic_derivation_refused(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError):
            await handle_derive_addresses(watch_only_config, ABANDON_MNEMONIC)

    async def test_xpub_extraction_refused(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError):
            await handle_derive_xpub(watch_only_config, ABANDON_MNEMONIC)

    async def test_bip85_refused(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError):
            await handle_derive_entropy_bip85(watch_only_config, ABANDON_MNEMONIC)

    async def test_wif_refused(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError):
            await handle_decode_wif(
                watch_only_config, "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ"
            )

    async def test_refusal_explains_the_alternative(self, watch_only_config) -> None:
        with pytest.raises(SecretsNotAllowedError) as exc:
            await handle_derive_addresses(watch_only_config, ABANDON_MNEMONIC)
        message = str(exc.value)
        assert "xpub" in message
        assert "MCP_BITCOIN_ALLOW_MNEMONIC" in message

    async def test_xpub_path_needs_no_permission(self, watch_only_config) -> None:
        """The safe path must work out of the box."""
        result = await handle_derive_addresses(watch_only_config, BIP84_MAINNET["zpub"], count=1)
        assert result["source_type"] == "xpub"
        assert result["addresses"][0]["address"] == BIP84_MAINNET["receive_0"]

    async def test_offline_tools_need_no_permission(self, watch_only_config) -> None:
        assert (await handle_validate_mnemonic(ABANDON_MNEMONIC))["valid"]


class TestDeriveAddresses:
    async def test_from_mnemonic_when_allowed(self, permissive_config) -> None:
        result = await handle_derive_addresses(permissive_config, ABANDON_MNEMONIC, count=2)
        assert result["source_type"] == "mnemonic"
        assert result["addresses"][0]["address"] == BIP84_MAINNET["receive_0"]

    async def test_xpub_and_mnemonic_agree(self, permissive_config) -> None:
        from_seed = await handle_derive_addresses(permissive_config, ABANDON_MNEMONIC, count=3)
        from_xpub = await handle_derive_addresses(permissive_config, BIP84_MAINNET["zpub"], count=3)
        assert [a["address"] for a in from_seed["addresses"]] == [
            a["address"] for a in from_xpub["addresses"]
        ]

    async def test_passphrase_changes_addresses(self, permissive_config) -> None:
        plain = await handle_derive_addresses(permissive_config, ABANDON_MNEMONIC, count=1)
        with_pass = await handle_derive_addresses(
            permissive_config, ABANDON_MNEMONIC, passphrase="TREZOR", count=1
        )
        assert plain["addresses"][0]["address"] != with_pass["addresses"][0]["address"]
        assert with_pass["passphrase_used"] is True

    async def test_rejects_empty_source(self, permissive_config) -> None:
        with pytest.raises(ValueError, match="source is required"):
            await handle_derive_addresses(permissive_config, "")

    async def test_rejects_invalid_mnemonic(self, permissive_config) -> None:
        with pytest.raises(ValueError, match="Invalid mnemonic"):
            await handle_derive_addresses(permissive_config, "abandon " * 11 + "abandon")


class TestDeriveFromPath:
    async def test_returns_every_format(self, permissive_config) -> None:
        result = await handle_derive_from_path(
            permissive_config, ABANDON_MNEMONIC, "m/84'/0'/0'/0/0"
        )
        assert result["addresses"]["native_segwit"] == BIP84_MAINNET["receive_0"]
        assert set(result["addresses"]) == {
            "legacy",
            "nested_segwit",
            "native_segwit",
            "taproot",
        }

    async def test_never_returns_private_key(self, permissive_config) -> None:
        """Private keys must not be echoed into the transcript."""
        result = await handle_derive_from_path(
            permissive_config, ABANDON_MNEMONIC, "m/84'/0'/0'/0/0"
        )
        assert "private_key_wif" not in result
        assert "private_key" not in result

    async def test_rejects_bad_path(self, permissive_config) -> None:
        with pytest.raises(ValueError, match="Invalid derivation path"):
            await handle_derive_from_path(permissive_config, ABANDON_MNEMONIC, "nope")


class TestDeriveXpub:
    async def test_matches_spec_vector(self, permissive_config) -> None:
        result = await handle_derive_xpub(permissive_config, ABANDON_MNEMONIC, "bip84")
        assert result["xpub"] == BIP84_MAINNET["zpub"]
        assert result["path"] == "m/84'/0'/0'"

    async def test_rejects_unknown_standard(self, permissive_config) -> None:
        with pytest.raises(ValueError, match="Unknown standard"):
            await handle_derive_xpub(permissive_config, ABANDON_MNEMONIC, "bip99")


class TestBip85Tool:
    async def test_child_mnemonic(self, permissive_config) -> None:
        result = await handle_derive_entropy_bip85(
            permissive_config, ABANDON_MNEMONIC, child_mnemonic_words=12
        )
        assert len(result["child_mnemonic"].split()) == 12
        assert result["path"] == "m/83696968'/39'/0'/12'/0'"

    async def test_raw_entropy_path_includes_length(self, permissive_config) -> None:
        result = await handle_derive_entropy_bip85(
            permissive_config, ABANDON_MNEMONIC, num_bytes=32
        )
        assert result["path"] == "m/83696968'/128169'/32'/0'"
        assert len(result["entropy_hex"]) == 64


class TestWif:
    async def test_decodes_without_echoing_secret(self, permissive_config) -> None:
        wif = "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ"
        result = await handle_decode_wif(permissive_config, wif, Network.MAINNET)
        assert "private_key_hex" not in result
        assert result["compressed"] is True
        assert result["addresses"]["native_segwit"].startswith("bc1q")

    async def test_rejects_wrong_network(self, permissive_config) -> None:
        wif = "L1aW4aubDFB7yfras2S1mN3bqg9nwySY8nkoLmJebSLD5BWv3ENZ"
        with pytest.raises(ValueError, match="mainnet"):
            await handle_decode_wif(permissive_config, wif, Network.TESTNET)


class TestPubkeyToAddress:
    async def test_single_type(self) -> None:
        pubkey = "0330d54fd0dd420a6e5f8d3624f5f3482cae350f79d5f0753bf5beef9c2d91af3c"
        result = await handle_get_address_from_pubkey(pubkey, "native_segwit")
        assert result["address"] == BIP84_MAINNET["receive_0"]

    async def test_all_types(self) -> None:
        pubkey = "0330d54fd0dd420a6e5f8d3624f5f3482cae350f79d5f0753bf5beef9c2d91af3c"
        result = await handle_get_address_from_pubkey(pubkey, "all")
        assert len(result["addresses"]) == 4

    async def test_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Invalid public key"):
            await handle_get_address_from_pubkey("nothex", "native_segwit")


class TestXpubDetection:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (BIP84_MAINNET["zpub"], True),
            ("xpub6CUGRUo...", True),
            ("tpubDCBWBSc...", True),
            (ABANDON_MNEMONIC, False),
            ("cabbage cat cloud crystal " * 3, False),
        ],
    )
    def test_detection(self, value, expected) -> None:
        """Mnemonics starting with 'c' must not be mistaken for keys.

        A prefix-character heuristic used to route ~150 'c' words into the WIF
        branch and crash.
        """
        assert looks_like_xpub(value) is expected
