# Contributing

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/junxit/mcp-bitcoin
cd mcp-bitcoin
uv sync --all-groups     # runtime + dev dependencies
```

## Checks

```bash
uv run pytest                          # tests
uv run pytest --cov --cov-report=term  # with coverage
uv run ruff check . && uv run ruff format --check .
uv run mypy src/
```

All four must pass; CI runs them on 3.11, 3.12 and 3.13.

## Testing philosophy

**Assert against published specifications, not against this implementation.**

This matters more here than in most projects, and the reason is concrete. An
earlier revision had a full green test suite while shipping a BIP-85 function
that produced entropy no other wallet would reproduce. The tests checked that
derivation was deterministic and that the output was well-formed. A wrong
implementation satisfies both.

Published vectors live in `tests/vectors.py`, each labeled with its source BIP.
When you add a derivation feature, find the spec's test vectors and use those.
If a feature genuinely has no published vectors, derive the expected value with
an independent tool and record in a comment how to reproduce it — see
`test_electrum.py::TestScriptHash::test_known_address` for the pattern.

A corollary: prefer constructing fixtures programmatically over pasting hex
literals. A transcribed constant that is subtly wrong fails for reasons that
have nothing to do with the behavior under test.

## Bitcoin-specific review points

- **Network correctness.** Anything touching an address must respect all four
  networks. Regtest is the one that gets forgotten — it uses testnet's version
  bytes but the `bcrt` bech32 prefix, so a two-way `is_mainnet` branch silently
  produces wrong addresses. Use `Network.embit_network`.
- **Never fabricate a financial value.** If a fee estimate is unavailable,
  raise. A plausible-looking default is worse than an error, because the caller
  will act on it.
- **Validate before querying.** Addresses are checked against the configured
  network before any backend call. Electrum indexes by script hash, which is
  identical across networks, so a wrong-network lookup returns a *plausible
  wrong answer* rather than an error.
- **Provider parity.** The same tool must return the same shape regardless of
  which backend answered. Failover happens mid-session.

## Adding a backend

Implement the `BitcoinProvider` protocol in `src/mcp_bitcoin/providers/`.
Methods your backend genuinely cannot serve should raise `NotImplementedError`
with a message naming a backend that *can* — those messages are surfaced to the
user when no provider can service a call.

If your backend speaks the Esplora REST API, subclass `EsploraFamilyProvider`
rather than reimplementing it.

## Scope

Transaction signing is deliberately out of scope for now; see `ROADMAP.md` for
the PSBT-based design. Please open an issue before starting work on it.

## Commits

Format: `<type>: <summary>` — `feat`, `fix`, `docs`, `test`, `refactor`,
`chore`. Update `changelog.txt` under `[Unreleased]` for anything user-facing.
