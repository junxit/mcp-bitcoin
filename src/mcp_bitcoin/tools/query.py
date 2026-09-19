"""On-chain query tool handlers."""

from __future__ import annotations

from typing import Any

from embit.script import Script
from embit.transaction import Transaction

from mcp_bitcoin.config import Network
from mcp_bitcoin.providers.base import ProviderManager
from mcp_bitcoin.types import (
    AddressInfo,
    Balance,
    BlockHeader,
    DecodedTransaction,
    ScriptInfo,
    TxInput,
    TxOutput,
    model_list,
)
from mcp_bitcoin.utils.units import sats_to_btc
from mcp_bitcoin.utils.validation import (
    networks_for_address,
    script_type_of,
    validate_address,
    validate_hex,
    validate_txid,
)

MAX_HISTORY_LIMIT = 100


async def handle_get_balance(pm: ProviderManager, address: str, network: Network) -> dict[str, Any]:
    validate_address(address, network)
    balance: Balance = await pm.call("get_balance", address)
    return balance.model_dump(exclude_none=True)


async def handle_get_utxos(
    pm: ProviderManager,
    address: str,
    min_confirmations: int = 0,
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    validate_address(address, network)
    if min_confirmations < 0:
        raise ValueError("min_confirmations must be non-negative")

    utxos = await pm.call("get_utxos", address)
    filtered = [u for u in utxos if u.confirmations >= min_confirmations]
    return {
        "address": address.strip(),
        "network": network.value,
        "utxo_count": len(filtered),
        "total_value_sats": sum(u.value_sats for u in filtered),
        "total_value_btc": sats_to_btc(sum(u.value_sats for u in filtered)),
        "min_confirmations": min_confirmations,
        "excluded_by_filter": len(utxos) - len(filtered),
        "utxos": model_list(filtered),
    }


async def handle_get_tx_history(
    pm: ProviderManager,
    address: str,
    limit: int = 25,
    after_txid: str | None = None,
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    validate_address(address, network)
    if not 1 <= limit <= MAX_HISTORY_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_HISTORY_LIMIT}, got {limit}")
    if after_txid:
        after_txid = validate_txid(after_txid)

    page = await pm.call("get_tx_history", address, limit, after_txid)
    result = {
        "address": address.strip(),
        "network": network.value,
        "returned": len(page.transactions),
        "has_more": page.has_more,
        "transactions": model_list(page.transactions),
        "source": page.source,
    }
    if page.has_more:
        result["next_cursor"] = page.next_cursor
        result["note"] = (
            "More transactions exist. Pass next_cursor as after_txid to fetch the following page."
        )
    return result


async def handle_get_transaction(
    pm: ProviderManager, txid: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    tx: DecodedTransaction = await pm.call("get_transaction", validate_txid(txid))
    return tx.model_dump(exclude_none=True)


async def handle_get_raw_transaction(
    pm: ProviderManager, txid: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    txid = validate_txid(txid)
    raw = await pm.call("get_raw_transaction", txid)
    return {"txid": txid, "hex": raw, "size_bytes": len(raw) // 2}


def _decode_transaction(raw: bytes, network: Network) -> dict[str, Any]:
    """Decode a raw transaction, resolving output addresses.

    Addresses are the point of decoding: showing amounts without destinations
    makes it impossible to check where money is actually going.
    """
    tx = Transaction.parse(raw)

    # vsize needs the witness-stripped size, which embit does not expose
    # directly, so re-serialize a witness-free copy to measure the base size.
    total_size = len(raw)
    if tx.is_segwit:
        stripped = Transaction(
            version=tx.version,
            vin=[type(i)(i.txid, i.vout, i.script_sig, i.sequence) for i in tx.vin],
            vout=list(tx.vout),
            locktime=tx.locktime,
        )
        base_size = len(stripped.serialize())
        weight = base_size * 3 + total_size
        vsize = (weight + 3) // 4
    else:
        base_size = total_size
        weight = total_size * 4
        vsize = total_size

    outputs = []
    for i, out in enumerate(tx.vout):
        script = out.script_pubkey
        try:
            address = script.address(network=network.embit_network)
        except Exception:
            address = None
        outputs.append(
            TxOutput(
                n=i,
                address=address,
                value_sats=out.value,
                value_btc=sats_to_btc(out.value),
                script_type=script_type_of(script),
                script_hex=script.data.hex(),
            )
        )

    inputs = [
        TxInput(
            txid=vin.txid.hex(),
            vout=vin.vout,
            sequence=vin.sequence,
            is_coinbase=vin.txid == b"\x00" * 32,
        )
        for vin in tx.vin
    ]

    total_out = sum(o.value_sats for o in outputs)
    return {
        "txid": tx.txid().hex(),
        "version": tx.version,
        "locktime": tx.locktime,
        "size": total_size,
        "vsize": vsize,
        "weight": weight,
        "is_segwit": tx.is_segwit,
        "input_count": len(inputs),
        "output_count": len(outputs),
        "inputs": model_list(inputs),
        "outputs": model_list(outputs),
        "total_output_sats": total_out,
        "total_output_btc": sats_to_btc(total_out),
        "network": network.value,
        "note": (
            "Fee cannot be computed from a raw transaction alone — it requires "
            "the value of each input being spent. Use get_transaction for a "
            "confirmed transaction to see its fee."
        ),
    }


async def handle_decode_raw_transaction(
    raw_tx: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    """Decode a raw transaction locally, with no network call."""
    raw = validate_hex(raw_tx, "raw_tx")
    if not raw:
        raise ValueError("raw_tx is empty")
    try:
        return _decode_transaction(raw, network)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError(f"Could not parse transaction: {exc}") from exc


async def handle_get_block(
    pm: ProviderManager, block: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    ref = (block or "").strip()
    if not ref:
        raise ValueError("block is required: a block hash or height")
    if not ref.isdigit():
        validate_txid(ref)  # block hashes share the 64-hex-character format
    header: BlockHeader = await pm.call("get_block", ref)
    return header.model_dump(exclude_none=True)


async def handle_get_block_height(
    pm: ProviderManager, network: Network = Network.MAINNET
) -> dict[str, Any]:
    header: BlockHeader = await pm.call("get_block_height")
    return header.model_dump(exclude_none=True)


async def handle_validate_address(
    address: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    """Validate an address and report its type and network.

    Reports rather than raises, so asking "is this valid?" about a
    wrong-network address gets an informative answer instead of an error.
    """
    address = (address or "").strip()
    candidates = networks_for_address(address)

    info = AddressInfo(
        address=address,
        valid=False,
        networks=[n.value for n in candidates],
    )

    if not candidates:
        info.error = "Unrecognized address prefix"
        return info.model_dump(exclude_none=True)

    try:
        from embit.script import address_to_scriptpubkey

        script = address_to_scriptpubkey(address)
    except Exception as exc:
        info.error = f"Invalid address: {exc}"
        return info.model_dump(exclude_none=True)

    info.valid = True
    info.script_hex = script.data.hex()
    info.address_type = script_type_of(script)
    info.matches_server_network = network in candidates

    result = info.model_dump(exclude_none=True)
    if not info.matches_server_network:
        result["warning"] = (
            f"This is a valid {'/'.join(result['networks'])} address, but the "
            f"server is configured for {network.value}. On-chain queries against "
            "it will be refused."
        )
    return result


async def handle_decode_script(
    script_hex: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    """Decode a scriptPubKey and identify its type and address."""
    raw = validate_hex(script_hex, "script_hex")
    if not raw:
        raise ValueError("script_hex is empty")

    script = Script(raw)
    try:
        address = script.address(network=network.embit_network)
    except Exception:
        address = None

    info = ScriptInfo(
        script_hex=raw.hex(),
        size=len(raw),
        script_type=script_type_of(script),
        address=address,
    )
    return info.model_dump(exclude_none=True)
