"""BIP-32 path parsing and extended key serialization."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.derivation.hd import (
    HARDENED_OFFSET,
    derive_key,
    key_from_xpub,
    parse_derivation_path,
    xpub_from_key,
)


class TestParseDerivationPath:
    def test_root(self) -> None:
        assert parse_derivation_path("m") == []

    def test_hardened_offset_applied(self) -> None:
        assert parse_derivation_path("m/84'") == [84 + HARDENED_OFFSET]

    def test_unhardened_left_alone(self) -> None:
        assert parse_derivation_path("m/0/1") == [0, 1]

    def test_h_and_apostrophe_equivalent(self) -> None:
        assert parse_derivation_path("m/84'/0'/0'") == parse_derivation_path("m/84h/0h/0h")

    def test_full_path(self) -> None:
        got = parse_derivation_path("m/84'/0'/0'/0/5")
        assert got == [
            84 + HARDENED_OFFSET,
            0 + HARDENED_OFFSET,
            0 + HARDENED_OFFSET,
            0,
            5,
        ]

    @pytest.mark.parametrize(
        "bad",
        [
            "84'/0'/0'",  # missing leading m
            "m/abc",
            "m/",
            "m//0",
            "m/-1",
            "n/0",
            "",
            "   ",
            "m/0''",
            "m/1.5",
        ],
    )
    def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(ValueError):
            parse_derivation_path(bad)

    def test_rejects_index_above_range(self) -> None:
        with pytest.raises(ValueError, match="out of range"):
            parse_derivation_path(f"m/{HARDENED_OFFSET}")


class TestDeriveKey:
    def test_validation_runs(self, master_key) -> None:
        """derive_key must validate, not hand raw input to embit."""
        with pytest.raises(ValueError, match="Invalid derivation path"):
            derive_key(master_key, "not a path")

    def test_root_returns_self(self, master_key) -> None:
        assert derive_key(master_key, "m").to_base58() == master_key.to_base58()

    def test_equivalent_paths_agree(self, master_key) -> None:
        a = derive_key(master_key, "m/84'/0'/0'/0/0")
        b = derive_key(derive_key(master_key, "m/84'/0'/0'"), "m/0/0")
        assert a.to_base58() == b.to_base58()


class TestExtendedKeys:
    @pytest.mark.parametrize(
        "network,standard,prefix",
        [
            (Network.MAINNET, "bip44", "xpub"),
            (Network.MAINNET, "bip49", "ypub"),
            (Network.MAINNET, "bip84", "zpub"),
            (Network.MAINNET, "bip86", "xpub"),
            (Network.TESTNET, "bip44", "tpub"),
            (Network.TESTNET, "bip49", "upub"),
            (Network.TESTNET, "bip84", "vpub"),
        ],
    )
    def test_version_prefix(self, master_key, network, standard, prefix) -> None:
        account = derive_key(master_key, f"m/84'/{network.coin_type}'/0'")
        assert xpub_from_key(account, standard, network).startswith(prefix)

    def test_roundtrip(self, master_key) -> None:
        account = derive_key(master_key, "m/84'/0'/0'")
        parsed = key_from_xpub(xpub_from_key(account, "bip84", Network.MAINNET))
        assert not parsed.is_private
        assert parsed.key.sec() == account.to_public().key.sec()

    def test_rejects_unknown_standard(self, master_key) -> None:
        with pytest.raises(ValueError, match="Unknown standard"):
            xpub_from_key(master_key, "bip999", Network.MAINNET)

    @pytest.mark.parametrize("bad", ["notanxpub", "xpub123", "", "zzzz"])
    def test_rejects_malformed_xpub(self, bad) -> None:
        with pytest.raises(ValueError, match="Invalid extended public key"):
            key_from_xpub(bad)
