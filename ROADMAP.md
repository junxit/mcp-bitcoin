# Roadmap

## Why v0.1 is read-only

An earlier development version of this project exposed `create_transaction`,
`create_transaction_from_address`, `sign_transaction` and
`broadcast_transaction`. An audit before the first public release found that
none of them had ever worked, and that the reasons were not superficial:

- `sign_transaction` called `Transaction.sign_input()`, which does not exist in
  embit. The `AttributeError` was swallowed by a bare `except`, so the function
  returned the **unsigned** transaction under a field named `signed_tx_hex`.
- Even with that fixed, the function could not have produced a valid signature.
  BIP-143 commits to the value of the output being spent, and a raw unsigned
  transaction does not contain input amounts. The function signature made the
  required data unavailable.
- Both builders passed a `network=` keyword to `address_to_scriptpubkey()`,
  which embit does not accept — a `TypeError` on every call.
- Input txids were byte-reversed twice, producing references to outpoints that
  do not exist.
- The builders relied on embit's mutable default arguments for `vin`/`vout`, so
  in a long-lived server process one transaction inherited the previous one's
  inputs and outputs.
- `create_transaction` accepted `change_address` and `fee_rate` and ignored
  both, meaning the entire input remainder would have been paid as fee.

The crash was masking the rest. Fixing only the obvious `TypeError` would have
turned a loud failure into silent fund loss.

Rather than ship a repaired-but-untested signing path in a public Bitcoin tool,
v0.1 removes it. What remains is verified against published specification
vectors.

## v0.2 — PSBT support

Transaction support returns built on PSBTs, which is the design where the
missing information actually exists.

- **Construct** a PSBT with fully populated `witness_utxo` /
  `non_witness_utxo`, so prevout amounts travel with the transaction.
- **Sign** with `PSBT.sign_with()` and finalize through `embit.finalizer`,
  rather than hand-rolling sighash construction.
- **Per-script-type fee estimation.** The old flat 68 vB/input assumed P2WPKH
  and underestimated a P2PKH input by more than 2x, which would have produced
  transactions below the relay minimum.
- **Per-script-type dust thresholds** instead of a hardcoded 546 sats, which
  burns up to 546 spendable sats on every P2WPKH change output.
- **Opt-in RBF** (`sequence=0xFFFFFFFD`). Every transaction the old code
  produced was non-replaceable, so an underpaid transaction could not be
  fee-bumped.
- **Mandatory pre-broadcast verification**: parse, confirm every input carries a
  signature or witness, and display destinations before sending.

Signing will stay behind an explicit opt-in flag, separate from
`MCP_BITCOIN_ALLOW_MNEMONIC`, and will be gated to testnet unless mainnet is
enabled deliberately.

Non-negotiable prerequisite: regtest integration tests that fund an address,
build, sign, broadcast and confirm. No signing code ships on the strength of
unit tests alone — that is precisely how the original defects survived.

## Later

- **MCP resources** (`bitcoin://address/{addr}`, `bitcoin://tx/{txid}`), for
  clients that prefer resource semantics over tool calls.
- **Multisig derivation** (BIP-45, BIP-48) and output descriptors.
- **TOML configuration** at `~/.config/mcp-bitcoin/config.toml`, for operators
  who would rather not put backend URLs in an MCP client config.
- **Recorded-response provider tests** covering Bitcoin Core and the REST
  backends, closing the last gap where a provider bug could reach a release.
- **PyPI publication**, once the tool surface has settled and real users have
  exercised it. Until then, install from git.
