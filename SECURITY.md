# Security

## The risk that is specific to this tool

`mcp-bitcoin` is an MCP server. Everything you pass to a tool becomes part of a
language model's input, and everything a tool returns becomes part of its
output.

If you paste a seed phrase into a tool call, that phrase:

- enters the model's context window;
- is transmitted to whatever inference provider your MCP client uses, which for
  a hosted client such as Claude Desktop means a third-party API over the
  network;
- is written to your client's conversation history on disk, and may be retained
  in server-side logs outside your control;
- may be summarized, cached, or replayed into later turns of the conversation.

None of that is a defect in this server, and no amount of care inside this
process can undo it. It is a structural property of putting a secret into a
prompt.

**Treat any key material that passes through this server as compromised.** If
it controls funds you would miss, it does not belong here.

## What this server does about it

**Watch-only by default.** Tools that accept a seed phrase or private key —
`generate_mnemonic`, `derive_xpub`, `derive_entropy_bip85`, `decode_wif`, and
the mnemonic branches of `derive_addresses` and `derive_from_path` — refuse to
run unless the operator sets `MCP_BITCOIN_ALLOW_MNEMONIC=true`. The refusal
explains the safer alternative rather than just failing.

**Private keys are never returned.** `decode_wif` reports the public key and
addresses. `derive_from_path` reports the public key and addresses. Neither
echoes the secret back into the transcript, because doing so doubles the
exposure while telling the caller nothing they did not already have.

**The xpub path is complete.** Every address-derivation and chain-query feature
works from an extended public key. You never need to give this server a seed
phrase to watch a wallet.

## The recommended setup

Derive an account xpub **once, on a machine that is not running this server**,
using an offline tool or your hardware wallet's export function. Then configure
`mcp-bitcoin` watch-only and pass the xpub:

```
derive_addresses(source="zpub6r...", address_type="native_segwit", count=20)
```

This gives you full address, balance, UTXO and history visibility with no
secret ever entering the model's context.

If you do need the seed-phrase tools — for recovery work, or exploring BIP-85 —
use a testnet or throwaway seed, or accept that the phrase is burned afterwards.

## Other properties

**Nothing is persisted.** No key material is written to disk, to a database, or
to a configuration file by this server. Key material exists only for the
duration of a tool call.

Python cannot reliably zero out memory: `bytes` objects are immutable and the
garbage collector may copy them. Earlier documentation for this project claimed
buffers were zeroized after use. That was never true and the claim has been
removed rather than restated.

**Log sanitization** redacts mnemonics, WIF keys (mainnet *and* testnet
prefixes), extended private keys, and URL credentials from log output. It is a
backstop against accidental logging, not a boundary — tool *results* do not pass
through it, which is why the tools refuse secrets rather than relying on
redaction.

**Network validation.** Every address is checked against the configured network
before any query is issued. Without this, an Electrum backend will happily
return a testnet balance for a mainnet address, because the script hash is
identical for both.

**TLS.** Electrum certificates are verified by default. Set
`MCP_BITCOIN_ELECTRUM_ALLOW_SELF_SIGNED=true` to accept self-signed
certificates — this leaves the connection encrypted but *unauthenticated*, so a
man-in-the-middle could return fabricated balances and fee rates. Verification
is skipped automatically for `.onion` hosts, where the v3 onion address is
itself the server's public key and authenticates the endpoint.

**Tor is fail-closed.** If `MCP_BITCOIN_TOR_PROXY` is set and the proxy is not
reachable at startup, the server exits rather than running with connections that
would fall outside Tor.

**RPC credentials.** Prefer Bitcoin Core's cookie authentication over embedding
`rpcuser:rpcpassword` in a URL that lands in an MCP client config file — those
files are routinely committed to git. The server reads
`~/.bitcoin/.cookie` (and the per-network equivalents) automatically when no
credentials are supplied. Credentials are redacted from all log output and from
`ping` / `list_backends` results.

## Scope

This server is read-only. It cannot spend, sign, or broadcast. The worst
outcome from a backend compromise is **wrong information** — a fabricated
balance, a misleading fee estimate — which could still lead you to make a bad
decision elsewhere. Cross-check anything that matters against a second source.

## Reporting a vulnerability

Open a [security advisory](https://github.com/junxit/mcp-bitcoin/security/advisories/new)
on GitHub. Please do not open a public issue for a vulnerability.

Include what you did, what happened, and what you expected. If it involves key
material, use a throwaway or testnet seed in your report — never a real one.

This is a personal project with no funded security team and no guaranteed
response time. It is offered under the Apache-2.0 license, without warranty.
