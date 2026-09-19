"""BIP-44/49/84/86 derivation against published vectors, across all networks."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.derivation.hd import derive_key, xpub_from_key
from mcp_bitcoin.derivation.standards import (
    build_account_path,
    derive_addresses,
    derive_addresses_from_xpub,
    standard_for_address_type,
)
from tests.vectors import (
    BIP44_MAINNET,
    BIP49_MAINNET,
    BIP84_MAINNET,
    BIP86_MAINNET,
)


class TestBIP84:
    """https://github.com/bitcoin/bips/blob/master/bip-0084.mediawiki"""

    def test_first_receive_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip84", Network.MAINNET, count=1)[0]
        assert got.address == BIP84_MAINNET["receive_0"]
        assert got.path == "m/84'/0'/0'/0/0"

    def test_second_receive_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip84", Network.MAINNET, start_index=1, count=1)[0]
        assert got.address == BIP84_MAINNET["receive_1"]

    def test_first_change_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip84", Network.MAINNET, change=1, count=1)[0]
        assert got.address == BIP84_MAINNET["change_0"]
        assert got.path == "m/84'/0'/0'/1/0"

    def test_account_zpub(self, master_key) -> None:
        account = derive_key(master_key, "m/84'/0'/0'")
        assert xpub_from_key(account, "bip84", Network.MAINNET) == BIP84_MAINNET["zpub"]


class TestBIP86:
    """https://github.com/bitcoin/bips/blob/master/bip-0086.mediawiki"""

    def test_first_receive_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip86", Network.MAINNET, count=1)[0]
        assert got.address == BIP86_MAINNET["receive_0"]

    def test_second_receive_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip86", Network.MAINNET, start_index=1, count=1)[0]
        assert got.address == BIP86_MAINNET["receive_1"]

    def test_first_change_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip86", Network.MAINNET, change=1, count=1)[0]
        assert got.address == BIP86_MAINNET["change_0"]


class TestBIP44AndBIP49:
    def test_bip44_addresses(self, master_key) -> None:
        got = derive_addresses(master_key, "bip44", Network.MAINNET, count=2)
        assert got[0].address == BIP44_MAINNET["receive_0"]
        assert got[1].address == BIP44_MAINNET["receive_1"]

    def test_bip49_address(self, master_key) -> None:
        got = derive_addresses(master_key, "bip49", Network.MAINNET, count=1)[0]
        assert got.address == BIP49_MAINNET["receive_0"]


class TestNetworkPrefixes:
    """Regtest must produce bcrt1 addresses, not tb1.

    Every embit lookup used to be a two-way ``is_mainnet`` branch, which
    silently mapped regtest onto testnet parameters.
    """

    @pytest.mark.parametrize(
        "network,standard,prefix",
        [
            (Network.MAINNET, "bip84", "bc1q"),
            (Network.MAINNET, "bip86", "bc1p"),
            (Network.TESTNET, "bip84", "tb1q"),
            (Network.TESTNET, "bip86", "tb1p"),
            (Network.SIGNET, "bip84", "tb1q"),
            (Network.REGTEST, "bip84", "bcrt1q"),
            (Network.REGTEST, "bip86", "bcrt1p"),
        ],
    )
    def test_bech32_prefix(self, master_key, network, standard, prefix) -> None:
        got = derive_addresses(master_key, standard, network, count=1)[0]
        assert got.address.startswith(prefix)

    @pytest.mark.parametrize(
        "network,first_chars",
        [
            (Network.MAINNET, ("1",)),
            (Network.TESTNET, ("m", "n")),
            (Network.REGTEST, ("m", "n")),
        ],
    )
    def test_legacy_prefix(self, master_key, network, first_chars) -> None:
        got = derive_addresses(master_key, "bip44", network, count=1)[0]
        assert got.address[0] in first_chars

    def test_coin_type_differs_by_network(self) -> None:
        assert build_account_path("bip84", Network.MAINNET) == "m/84'/0'/0'"
        assert build_account_path("bip84", Network.TESTNET) == "m/84'/1'/0'"
        assert build_account_path("bip84", Network.REGTEST) == "m/84'/1'/0'"


class TestWatchOnlyEquivalence:
    """An xpub must derive exactly what the seed does."""

    @pytest.mark.parametrize(
        "standard,address_type",
        [
            ("bip44", "legacy"),
            ("bip49", "nested_segwit"),
            ("bip84", "native_segwit"),
            ("bip86", "taproot"),
        ],
    )
    def test_xpub_matches_mnemonic(self, master_key, standard, address_type) -> None:
        from_seed = derive_addresses(master_key, standard, Network.MAINNET, count=5)
        account = derive_key(master_key, build_account_path(standard, Network.MAINNET))
        from_xpub = derive_addresses_from_xpub(
            account.to_public(), address_type, Network.MAINNET, count=5
        )
        assert [a.address for a in from_seed] == [a.address for a in from_xpub]

    def test_paths_are_absolute_when_account_known(self, master_key) -> None:
        account = derive_key(master_key, "m/84'/0'/0'")
        got = derive_addresses_from_xpub(
            account.to_public(),
            "native_segwit",
            Network.MAINNET,
            count=1,
            account_path="m/84'/0'/0'",
        )
        assert got[0].path == "m/84'/0'/0'/0/0"


class TestValidation:
    def test_rejects_unknown_address_type(self) -> None:
        with pytest.raises(ValueError, match="Unknown address type"):
            standard_for_address_type("ethereum")

    def test_rejects_unknown_standard(self) -> None:
        with pytest.raises(ValueError, match="Unknown standard"):
            build_account_path("bip99", Network.MAINNET)

    @pytest.mark.parametrize("change", [-1, 2, 99])
    def test_rejects_bad_change(self, master_key, change) -> None:
        with pytest.raises(ValueError, match="change"):
            derive_addresses(master_key, "bip84", Network.MAINNET, change=change)

    @pytest.mark.parametrize("count", [0, -1, 501])
    def test_rejects_bad_count(self, master_key, count) -> None:
        with pytest.raises(ValueError, match="count"):
            derive_addresses(master_key, "bip84", Network.MAINNET, count=count)

    def test_rejects_negative_account(self) -> None:
        with pytest.raises(ValueError, match="Account"):
            build_account_path("bip84", Network.MAINNET, account=-1)

    def test_addresses_are_unique(self, master_key) -> None:
        got = derive_addresses(master_key, "bip84", Network.MAINNET, count=50)
        assert len({a.address for a in got}) == 50
