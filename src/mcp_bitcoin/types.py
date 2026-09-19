"""Shared data models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Balance(BaseModel):
    address: str
    confirmed_sats: int
    unconfirmed_sats: int
    total_sats: int
    confirmed_btc: str
    unconfirmed_btc: str
    total_btc: str
    source: str | None = None


class UTXO(BaseModel):
    txid: str
    vout: int
    value_sats: int
    value_btc: str
    confirmations: int
    block_height: int | None = None
    script_hex: str = ""


class TxSummary(BaseModel):
    txid: str
    block_height: int | None = None
    confirmations: int = 0
    fee_sats: int | None = None
    size: int | None = None
    vsize: int | None = None
    timestamp: int | None = None


class TxHistoryPage(BaseModel):
    """One page of address history.

    Esplora-family APIs cap a single response at ~25 confirmed transactions and
    paginate by "last seen txid" rather than a numeric offset. Carrying the
    cursor explicitly keeps a truncated page from reading as "no more history".
    """

    transactions: list[TxSummary] = []
    has_more: bool = False
    next_cursor: str | None = None
    source: str | None = None


class FeeEstimate(BaseModel):
    target_blocks: int
    sat_per_vbyte: float
    estimated_fee_p2wpkh_1in_1out_sats: int
    estimated_fee_p2wpkh_2in_2out_sats: int
    source: str | None = None


class FeeTiers(BaseModel):
    economy: float
    normal: float
    priority: float
    unit: str = "sat/vB"
    source: str | None = None


class MempoolInfo(BaseModel):
    tx_count: int
    vsize: int
    total_fee_sats: int | None = None
    memory_usage_bytes: int | None = None
    max_mempool_bytes: int | None = None
    min_fee_rate_sat_vb: float | None = None
    source: str | None = None


class BlockHeader(BaseModel):
    hash: str
    height: int
    timestamp: int
    version: int | None = None
    nonce: int | None = None
    bits: str | None = None
    difficulty: float | None = None
    tx_count: int | None = None
    size: int | None = None
    weight: int | None = None
    merkle_root: str | None = None
    prev_hash: str | None = None
    confirmations: int | None = None
    source: str | None = None


class TxInput(BaseModel):
    txid: str | None = None
    vout: int | None = None
    address: str | None = None
    value_sats: int | None = None
    value_btc: str | None = None
    is_coinbase: bool = False
    sequence: int | None = None


class TxOutput(BaseModel):
    n: int
    address: str | None = None
    value_sats: int
    value_btc: str
    script_type: str | None = None
    script_hex: str | None = None


class DecodedTransaction(BaseModel):
    txid: str
    version: int
    locktime: int
    size: int | None = None
    vsize: int | None = None
    weight: int | None = None
    fee_sats: int | None = None
    fee_rate_sat_vb: float | None = None
    is_segwit: bool = False
    inputs: list[TxInput] = []
    outputs: list[TxOutput] = []
    total_output_sats: int = 0
    confirmations: int | None = None
    block_hash: str | None = None
    block_height: int | None = None
    block_time: int | None = None
    source: str | None = None


class ProviderStatus(BaseModel):
    name: str
    type: str
    url: str
    reachable: bool
    degraded: bool = False
    latency_ms: float | None = None
    error: str | None = None


class DerivedAddress(BaseModel):
    path: str
    address: str
    public_key: str
    address_type: str


class AddressInfo(BaseModel):
    address: str
    valid: bool
    address_type: str | None = None
    networks: list[str] = []
    matches_server_network: bool | None = None
    script_hex: str | None = None
    error: str | None = None


class PsbtInput(BaseModel):
    index: int
    txid: str | None = None
    vout: int | None = None
    address: str | None = None
    value_sats: int | None = None
    value_btc: str | None = None
    has_signature: bool = False
    signature_count: int = 0


class DecodedPsbt(BaseModel):
    version: int
    locktime: int
    input_count: int
    output_count: int
    inputs: list[PsbtInput] = []
    outputs: list[TxOutput] = []
    total_input_sats: int | None = None
    total_output_sats: int = 0
    fee_sats: int | None = None
    fee_btc: str | None = None
    is_complete: bool = False
    warnings: list[str] = []


class ScriptInfo(BaseModel):
    script_hex: str
    size: int
    script_type: str
    address: str | None = None
    asm: str | None = None


def model_list(items: list[Any]) -> list[dict[str, Any]]:
    """Dump a list of pydantic models, dropping unset optional fields."""
    return [i.model_dump(exclude_none=True) for i in items]
