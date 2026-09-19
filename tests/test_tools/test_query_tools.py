"""Query tool handlers: decoding, validation and network enforcement."""

from __future__ import annotations

import pytest

from mcp_bitcoin.config import Network
from mcp_bitcoin.tools.query import (
    handle_decode_raw_transaction,
    handle_decode_script,
    handle_get_balance,
    handle_validate_address,
)
from mcp_bitcoin.utils.units import convert_units
from tests.conftest import FakeProvider
from tests.vectors import KNOWN_ADDRESSES, REGTEST_ADDRESSES


def build_legacy_tx() -> str:
    """A P2PKH-output transaction with no witness data.

    Built with embit rather than pasted as a hex literal: a hand-copied
    constant that happens to be malformed would fail for reasons unrelated to
    what the test is checking.
    """
    from embit.script import Script
    from embit.transaction import Transaction, TransactionInput, TransactionOutput

    return (
        Transaction(
            version=1,
            vin=[TransactionInput(bytes.fromhex("aa" * 32), 0)],
            vout=[
                TransactionOutput(
                    5_000_000_000,
                    Script(bytes.fromhex("76a914" + "11" * 20 + "88ac")),
                ),
                TransactionOutput(
                    4_000_000_000,
                    Script(bytes.fromhex("76a914" + "22" * 20 + "88ac")),
                ),
            ],
            locktime=0,
        )
        .serialize()
        .hex()
    )


def build_segwit_tx() -> str:
    """A P2WPKH-input transaction carrying witness data."""
    from embit.script import Script
    from embit.transaction import (
        Transaction,
        TransactionInput,
        TransactionOutput,
        Witness,
    )

    vin = TransactionInput(bytes.fromhex("bb" * 32), 0)
    vin.witness = Witness([b"\x30" * 71, b"\x02" * 33])
    return (
        Transaction(
            version=2,
            vin=[vin],
            vout=[TransactionOutput(50_000, Script(bytes.fromhex("0014" + "11" * 20)))],
            locktime=0,
        )
        .serialize()
        .hex()
    )


TX_LEGACY_HEX = build_legacy_tx()
TX_SEGWIT_HEX = build_segwit_tx()


class TestValidateAddressTool:
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
    async def test_identifies_mainnet_types(self, key, expected) -> None:
        result = await handle_validate_address(KNOWN_ADDRESSES[key], Network.MAINNET)
        assert result["valid"]
        assert result["address_type"] == expected
        assert result["matches_server_network"] is True

    async def test_reports_wrong_network_without_raising(self) -> None:
        """Asking 'is this valid?' should answer, not error."""
        result = await handle_validate_address(KNOWN_ADDRESSES["p2wpkh"], Network.TESTNET)
        assert result["valid"] is True
        assert result["matches_server_network"] is False
        assert "warning" in result

    async def test_regtest_recognized(self) -> None:
        result = await handle_validate_address(REGTEST_ADDRESSES["p2wpkh"], Network.REGTEST)
        assert result["valid"]
        assert result["networks"] == ["regtest"]

    @pytest.mark.parametrize("bad", ["", "notanaddress", "ltc1qxyz"])
    async def test_invalid_addresses(self, bad) -> None:
        result = await handle_validate_address(bad, Network.MAINNET)
        assert result["valid"] is False
        assert "error" in result


class TestBalanceNetworkEnforcement:
    async def test_rejects_wrong_network_before_querying(self, make_manager) -> None:
        """The backend must never see a wrong-network address."""
        provider = FakeProvider("rest")
        pm = make_manager(provider)
        with pytest.raises(ValueError, match="mainnet address"):
            await handle_get_balance(pm, KNOWN_ADDRESSES["p2wpkh"], Network.TESTNET)
        assert provider.calls == []

    async def test_allows_matching_network(self, make_manager) -> None:
        pm = make_manager(FakeProvider("rest"))
        result = await handle_get_balance(pm, KNOWN_ADDRESSES["p2wpkh"], Network.MAINNET)
        assert result["total_sats"] == 100_000


class TestDecodeRawTransaction:
    async def test_decodes_legacy_transaction(self) -> None:
        result = await handle_decode_raw_transaction(TX_LEGACY_HEX, Network.MAINNET)
        assert result["version"] == 1
        assert result["input_count"] == 1
        assert result["output_count"] == 2
        assert result["is_segwit"] is False

    async def test_non_segwit_vsize_equals_size(self) -> None:
        result = await handle_decode_raw_transaction(TX_LEGACY_HEX, Network.MAINNET)
        assert result["vsize"] == result["size"]
        assert result["weight"] == result["size"] * 4

    async def test_shows_output_addresses(self) -> None:
        """Amounts without destinations make verification impossible."""
        result = await handle_decode_raw_transaction(TX_LEGACY_HEX, Network.MAINNET)
        assert all(o["address"].startswith("1") for o in result["outputs"])
        assert all(o["script_type"] == "P2PKH" for o in result["outputs"])
        assert result["total_output_sats"] == 9_000_000_000

    async def test_renders_addresses_per_network(self) -> None:
        mainnet = await handle_decode_raw_transaction(TX_SEGWIT_HEX, Network.MAINNET)
        regtest = await handle_decode_raw_transaction(TX_SEGWIT_HEX, Network.REGTEST)
        assert mainnet["outputs"][0]["address"].startswith("bc1q")
        assert regtest["outputs"][0]["address"].startswith("bcrt1q")

    @pytest.mark.parametrize("bad", ["", "nothex", "zz"])
    async def test_rejects_malformed(self, bad) -> None:
        with pytest.raises(ValueError):
            await handle_decode_raw_transaction(bad, Network.MAINNET)

    async def test_segwit_vsize_is_below_size(self) -> None:
        """A segwit tx must report vsize < size, via the witness discount.

        Reporting total size as vsize inflated it by ~57% and broke any
        downstream sat/vB reasoning, such as deciding whether to fee-bump.
        """
        result = await handle_decode_raw_transaction(TX_SEGWIT_HEX, Network.MAINNET)
        assert result["is_segwit"] is True
        assert result["vsize"] < result["size"]

    async def test_segwit_weight_formula(self) -> None:
        """weight = base*3 + total, and vsize = ceil(weight/4)."""
        import math

        result = await handle_decode_raw_transaction(TX_SEGWIT_HEX, Network.MAINNET)
        assert result["vsize"] == math.ceil(result["weight"] / 4)
        assert result["weight"] < result["size"] * 4


class TestDecodeScript:
    async def test_p2wpkh(self) -> None:
        result = await handle_decode_script("0014" + "11" * 20, Network.MAINNET)
        assert result["script_type"] == "P2WPKH"
        assert result["address"].startswith("bc1q")

    async def test_regtest_address_rendering(self) -> None:
        result = await handle_decode_script("0014" + "11" * 20, Network.REGTEST)
        assert result["address"].startswith("bcrt1")

    async def test_rejects_bad_hex(self) -> None:
        with pytest.raises(ValueError, match="Invalid hex"):
            await handle_decode_script("nothex", Network.MAINNET)


class TestUnitConversion:
    @pytest.mark.parametrize(
        "amount,src,dst,expected",
        [
            ("1", "btc", "sat", "100000000"),
            ("100000000", "sat", "btc", "1.00000000"),
            ("1", "mbtc", "sat", "100000"),
            ("0.001", "btc", "sat", "100000"),
            ("1", "btc", "bit", "1000000.00"),
        ],
    )
    def test_conversions(self, amount, src, dst, expected) -> None:
        assert convert_units(amount, src, dst) == expected

    def test_rejects_unknown_unit(self) -> None:
        with pytest.raises(ValueError, match="Unknown"):
            convert_units("1", "eth", "btc")

    def test_rejects_negative(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            convert_units("-1", "btc", "sat")

    def test_rejects_above_supply_cap(self) -> None:
        with pytest.raises(ValueError, match="supply cap"):
            convert_units("21000001", "btc", "sat")

    def test_rejects_fractional_satoshi(self) -> None:
        """Satoshis are indivisible; silently rounding would lose value."""
        with pytest.raises(ValueError, match="indivisible"):
            convert_units("0.000000001", "btc", "sat")

    def test_no_float_rounding_error(self) -> None:
        assert convert_units("0.1", "btc", "sat") == "10000000"
        assert convert_units("0.29", "btc", "sat") == "29000000"
