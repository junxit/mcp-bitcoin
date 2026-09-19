"""PSBT decoding.

The decoder exists so a human can answer "where is this money going?" before
signing. Outputs without addresses, or a missing fee, would let a malicious
PSBT pass review — so those are the properties under test.
"""

from __future__ import annotations

import pytest
from embit.psbt import PSBT
from embit.script import Script
from embit.transaction import Transaction, TransactionInput, TransactionOutput

from mcp_bitcoin.config import Network
from mcp_bitcoin.tools.psbt import handle_decode_psbt

ALICE_SPK = Script(bytes.fromhex("0014" + "11" * 20))
BOB_SPK = Script(bytes.fromhex("0014" + "22" * 20))
CHANGE_SPK = Script(bytes.fromhex("0014" + "33" * 20))


def build_psbt(
    *,
    input_value: int = 100_000,
    outputs: list[tuple[int, Script]] | None = None,
    with_utxo: bool = True,
) -> str:
    """Build a single-input PSBT for testing."""
    outputs = outputs or [(90_000, BOB_SPK)]
    tx = Transaction(
        version=2,
        vin=[TransactionInput(bytes.fromhex("ab" * 32), 0)],
        vout=[TransactionOutput(value, spk) for value, spk in outputs],
        locktime=0,
    )
    psbt = PSBT(tx)
    if with_utxo:
        psbt.inputs[0].witness_utxo = TransactionOutput(input_value, ALICE_SPK)
    return psbt.to_base64()


class TestParsing:
    @pytest.mark.parametrize("bad", ["", "   ", "not base64!!!", "aGVsbG8="])
    async def test_rejects_garbage(self, bad) -> None:
        with pytest.raises(ValueError):
            await handle_decode_psbt(bad, Network.MAINNET)

    async def test_rejects_non_psbt_base64(self) -> None:
        import base64

        payload = base64.b64encode(b"this is not a psbt at all").decode()
        with pytest.raises(ValueError, match=r"not a PSBT|Could not parse"):
            await handle_decode_psbt(payload, Network.MAINNET)

    async def test_accepts_valid_psbt(self) -> None:
        result = await handle_decode_psbt(build_psbt(), Network.MAINNET)
        assert result["input_count"] == 1
        assert result["output_count"] == 1


class TestDestinationVisibility:
    async def test_shows_output_addresses(self) -> None:
        """Without addresses, a swapped destination is invisible."""
        result = await handle_decode_psbt(build_psbt(), Network.MAINNET)
        assert result["outputs"][0]["address"].startswith("bc1q")
        assert result["outputs"][0]["value_sats"] == 90_000

    async def test_shows_input_address_and_value(self) -> None:
        result = await handle_decode_psbt(build_psbt(), Network.MAINNET)
        assert result["inputs"][0]["address"].startswith("bc1q")
        assert result["inputs"][0]["value_sats"] == 100_000

    async def test_renders_addresses_for_network(self) -> None:
        psbt = build_psbt()
        mainnet = await handle_decode_psbt(psbt, Network.MAINNET)
        regtest = await handle_decode_psbt(psbt, Network.REGTEST)
        assert mainnet["outputs"][0]["address"].startswith("bc1q")
        assert regtest["outputs"][0]["address"].startswith("bcrt1q")

    async def test_multiple_outputs_all_shown(self) -> None:
        result = await handle_decode_psbt(
            build_psbt(outputs=[(50_000, BOB_SPK), (40_000, CHANGE_SPK)]),
            Network.MAINNET,
        )
        assert result["output_count"] == 2
        assert len({o["address"] for o in result["outputs"]}) == 2


class TestFee:
    async def test_computes_fee(self) -> None:
        result = await handle_decode_psbt(
            build_psbt(input_value=100_000, outputs=[(90_000, BOB_SPK)]),
            Network.MAINNET,
        )
        assert result["fee_sats"] == 10_000
        assert result["total_input_sats"] == 100_000
        assert result["total_output_sats"] == 90_000

    async def test_warns_on_excessive_fee(self) -> None:
        """A fee larger than a tenth of the spend is probably a mistake."""
        result = await handle_decode_psbt(
            build_psbt(input_value=100_000, outputs=[(10_000, BOB_SPK)]),
            Network.MAINNET,
        )
        assert any("fee" in w.lower() for w in result["warnings"])

    async def test_no_warning_on_reasonable_fee(self) -> None:
        result = await handle_decode_psbt(
            build_psbt(input_value=100_000, outputs=[(99_000, BOB_SPK)]),
            Network.MAINNET,
        )
        assert not any("more than" in w for w in result["warnings"])

    async def test_warns_when_fee_unverifiable(self) -> None:
        """No UTXO data means the fee cannot be checked — say so."""
        result = await handle_decode_psbt(build_psbt(with_utxo=False), Network.MAINNET)
        assert "fee_sats" not in result or result.get("fee_sats") is None
        assert any("cannot be verified" in w for w in result["warnings"])


class TestSigningStatus:
    async def test_unsigned_is_incomplete(self) -> None:
        result = await handle_decode_psbt(build_psbt(), Network.MAINNET)
        assert result["is_complete"] is False
        assert result["inputs"][0]["has_signature"] is False

    async def test_reports_note_about_not_signing(self) -> None:
        result = await handle_decode_psbt(build_psbt(), Network.MAINNET)
        assert "cannot sign" in result["note"]
