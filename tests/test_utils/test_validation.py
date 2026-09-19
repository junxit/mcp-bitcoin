"""Address and input validation.

The central guarantee here: an address for the wrong network is *rejected*,
not silently queried against the wrong chain.
"""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.utils.validation import (
    networks_for_address,
    script_type_of,
    validate_address,
    validate_hex,
    validate_txid,
)
from tests.vectors import KNOWN_ADDRESSES, REGTEST_ADDRESSES, TESTNET_ADDRESSES


class TestNetworkDetection:
    @pytest.mark.parametrize(
        "address,expected",
        [
            (KNOWN_ADDRESSES["p2pkh"], Network.MAINNET),
            (KNOWN_ADDRESSES["p2sh"], Network.MAINNET),
            (KNOWN_ADDRESSES["p2wpkh"], Network.MAINNET),
            (KNOWN_ADDRESSES["p2tr"], Network.MAINNET),
            (TESTNET_ADDRESSES["p2wpkh"], Network.TESTNET),
            (REGTEST_ADDRESSES["p2wpkh"], Network.REGTEST),
        ],
    )
    def test_detects_network(self, address, expected) -> None:
        assert expected in networks_for_address(address)

    def test_bcrt_beats_bc_prefix(self) -> None:
        """'bcrt1' must not be matched as 'bc1' — longest prefix wins."""
        nets = networks_for_address(REGTEST_ADDRESSES["p2wpkh"])
        assert nets == (Network.REGTEST,)
        assert Network.MAINNET not in nets

    def test_testnet_and_signet_share_prefixes(self) -> None:
        nets = networks_for_address(TESTNET_ADDRESSES["p2wpkh"])
        assert Network.TESTNET in nets
        assert Network.SIGNET in nets

    def test_unknown_prefix(self) -> None:
        assert networks_for_address("ltc1qsomething") == ()


class TestValidateAddress:
    @pytest.mark.parametrize("address", list(KNOWN_ADDRESSES.values()))
    def test_accepts_mainnet_on_mainnet(self, address) -> None:
        assert validate_address(address, Network.MAINNET) is not None

    def test_rejects_mainnet_address_on_testnet(self) -> None:
        """The core cross-network guard.

        Without it, Electrum would hash the script and return a testnet balance
        for a mainnet address, which reads as "your funds are gone".
        """
        with pytest.raises(ValueError, match="mainnet address"):
            validate_address(KNOWN_ADDRESSES["p2wpkh"], Network.TESTNET)

    def test_rejects_testnet_address_on_mainnet(self) -> None:
        with pytest.raises(ValueError, match="configured for mainnet"):
            validate_address(TESTNET_ADDRESSES["p2wpkh"], Network.MAINNET)

    def test_rejects_regtest_address_on_testnet(self) -> None:
        with pytest.raises(ValueError, match="regtest"):
            validate_address(REGTEST_ADDRESSES["p2wpkh"], Network.TESTNET)

    def test_accepts_testnet_address_on_signet(self) -> None:
        assert validate_address(TESTNET_ADDRESSES["p2wpkh"], Network.SIGNET) is not None

    @pytest.mark.parametrize("bad", ["", "   ", "notanaddress", "ltc1qxyz"])
    def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(ValueError):
            validate_address(bad, Network.MAINNET)

    def test_rejects_bad_checksum(self) -> None:
        corrupted = KNOWN_ADDRESSES["p2wpkh"][:-1] + "x"
        with pytest.raises(ValueError):
            validate_address(corrupted, Network.MAINNET)

    def test_tolerates_surrounding_whitespace(self) -> None:
        padded = f"  {KNOWN_ADDRESSES['p2wpkh']}  "
        assert validate_address(padded, Network.MAINNET) is not None


class TestScriptType:
    @pytest.mark.parametrize(
        "key,expected",
        [
            ("p2pkh", "P2PKH"),
            ("p2sh", "P2SH"),
            ("p2wpkh", "P2WPKH"),
            ("p2wsh", "P2WSH"),
            ("p2tr", "P2TR"),
        ],
    )
    def test_identifies_type(self, key, expected) -> None:
        script = validate_address(KNOWN_ADDRESSES[key], Network.MAINNET)
        assert script_type_of(script) == expected


class TestTxid:
    def test_accepts_and_lowercases(self) -> None:
        txid = "A" * 64
        assert validate_txid(txid) == "a" * 64

    @pytest.mark.parametrize("bad", ["", "abc", "z" * 64, "a" * 63, "a" * 65])
    def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(ValueError, match="Invalid txid"):
            validate_txid(bad)


class TestHex:
    def test_decodes(self) -> None:
        assert validate_hex("deadbeef") == b"\xde\xad\xbe\xef"

    @pytest.mark.parametrize("bad", ["xyz", "abc", "not hex"])
    def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(ValueError, match="Invalid hex"):
            validate_hex(bad, "field")
