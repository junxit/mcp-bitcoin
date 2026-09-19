"""PSBT inspection.

Read-only by design. Building and signing transactions is deferred to a later
release (see ROADMAP.md) because doing it correctly requires the prevout
amounts and scriptCodes that only a fully-populated PSBT carries.

The job here is to answer "where is this money actually going?" — so output
addresses and the fee are mandatory, not optional detail. A decoder that shows
only amounts lets a malicious PSBT pass review.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any

from embit.psbt import PSBT

from mcp_bitcoin.config import Network
from mcp_bitcoin.types import DecodedPsbt, PsbtInput, TxOutput
from mcp_bitcoin.utils.units import sats_to_btc
from mcp_bitcoin.utils.validation import script_type_of

# A fee above this share of the total spend is more likely a mistake than intent.
FEE_WARNING_RATIO = 0.10
MAX_PSBT_BYTES = 1_000_000


def _parse_psbt(psbt_b64: str) -> PSBT:
    """Parse a base64 PSBT, rejecting malformed input loudly.

    ``base64.b64decode`` silently discards characters outside the alphabet by
    default, so a corrupted PSBT can decode to different-but-parseable bytes.
    ``validate=True`` makes that an error instead.
    """
    text = (psbt_b64 or "").strip()
    if not text:
        raise ValueError("psbt is empty")

    try:
        return PSBT.from_base64(text)
    except Exception:
        pass  # Fall through to give a more specific diagnosis below.

    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"Not valid base64: {exc}. A PSBT normally starts with 'cHNidP'.") from exc

    if len(raw) > MAX_PSBT_BYTES:
        raise ValueError(f"PSBT is too large ({len(raw)} bytes)")
    if not raw.startswith(b"psbt\xff"):
        raise ValueError("Decoded data is not a PSBT (missing the 'psbt\\xff' magic bytes).")

    try:
        return PSBT.parse(raw)
    except Exception as exc:
        raise ValueError(f"Could not parse PSBT: {exc}") from exc


def _address_of(script: Any, network: Network) -> str | None:
    try:
        return str(script.address(network=network.embit_network))
    except Exception:
        return None


async def handle_decode_psbt(psbt_b64: str, network: Network = Network.MAINNET) -> dict[str, Any]:
    """Decode a PSBT and show its inputs, destinations, fee and signing status."""
    psbt = _parse_psbt(psbt_b64)
    tx = psbt.tx
    warnings: list[str] = []

    inputs: list[PsbtInput] = []
    total_in = 0
    amounts_known = True

    for i, scope in enumerate(psbt.inputs):
        entry = PsbtInput(
            index=i,
            txid=scope.vin.txid.hex() if scope.vin else None,
            vout=scope.vin.vout if scope.vin else None,
        )

        # .utxo resolves witness_utxo or non_witness_utxo, so legacy inputs
        # report their value too.
        utxo = None
        try:
            utxo = scope.utxo
        except Exception:
            utxo = None

        if utxo is not None:
            entry.value_sats = utxo.value
            entry.value_btc = sats_to_btc(utxo.value)
            entry.address = _address_of(utxo.script_pubkey, network)
            total_in += utxo.value
        else:
            amounts_known = False

        sigs = getattr(scope, "partial_sigs", None) or {}
        entry.signature_count = len(sigs)
        entry.has_signature = bool(sigs)
        inputs.append(entry)

    if not amounts_known:
        warnings.append(
            "At least one input has no UTXO data, so the fee cannot be verified. "
            "Do not sign a PSBT whose fee you cannot check."
        )

    outputs = [
        TxOutput(
            n=i,
            address=_address_of(out.script_pubkey, network),
            value_sats=out.value,
            value_btc=sats_to_btc(out.value),
            script_type=script_type_of(out.script_pubkey),
            script_hex=out.script_pubkey.data.hex(),
        )
        for i, out in enumerate(tx.vout)
    ]
    total_out = sum(o.value_sats for o in outputs)

    fee = None
    try:
        fee = psbt.fee()
    except Exception:
        if amounts_known:
            fee = total_in - total_out

    if any(o.address is None for o in outputs):
        warnings.append(
            "One or more outputs use a script with no standard address form. "
            "Inspect script_hex before signing."
        )

    if fee is not None and fee < 0:
        warnings.append(f"Outputs exceed inputs by {abs(fee)} sats. This PSBT is invalid.")
    elif fee is not None and total_out > 0 and fee > total_out * FEE_WARNING_RATIO:
        warnings.append(
            f"The fee ({fee} sats) is more than "
            f"{int(FEE_WARNING_RATIO * 100)}% of the amount being sent "
            f"({total_out} sats). Confirm this is intended."
        )

    signed = sum(1 for i in inputs if i.has_signature)
    if signed and signed < len(inputs):
        warnings.append(f"Partially signed: {signed} of {len(inputs)} inputs have signatures.")

    decoded = DecodedPsbt(
        version=tx.version,
        locktime=tx.locktime,
        input_count=len(inputs),
        output_count=len(outputs),
        inputs=inputs,
        outputs=outputs,
        total_input_sats=total_in if amounts_known else None,
        total_output_sats=total_out,
        fee_sats=fee,
        fee_btc=sats_to_btc(fee) if fee is not None and fee >= 0 else None,
        is_complete=bool(inputs) and signed == len(inputs),
        warnings=warnings,
    )

    result = decoded.model_dump(exclude_none=True)
    result["network"] = network.value
    result["note"] = (
        "Check every destination address against what you expect before "
        "signing this PSBT in your wallet. This server cannot sign it."
    )
    return result
