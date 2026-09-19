# mcp-bitcoin

An [MCP](https://modelcontextprotocol.io) server that gives Claude — or any
MCP-compatible client — a read-only window into Bitcoin: HD wallet derivation,
address and UTXO queries, transaction lookup, fee estimation and mempool
analysis, against your own node or public APIs, over Tor if you want it.

> **Status: pre-alpha (v0.1.0).** The derivation engine is verified against the
> published BIP test vectors and the query layer has been exercised against
> live APIs. It has not seen broad real-world use. Treat results as
> informational and cross-check anything that matters.

---

## ⚠️ Read this before you use a seed phrase

**Anything you pass to an MCP tool goes into a language model's context.**

If you paste a seed phrase into a tool call, that phrase is transmitted to
whatever inference provider your client uses — for a hosted client, a
third-party API over the network — and written into your client's saved
conversation history. It may be cached or retained in logs you do not control.

No amount of care inside this server can undo that. It is a property of putting
a secret in a prompt.

**So this server is watch-only by default.** Every tool that accepts a seed
phrase or private key refuses to run unless you explicitly set
`MCP_BITCOIN_ALLOW_MNEMONIC=true`.

You almost certainly don't need to. Derive an account **xpub** once on a
machine that isn't running this server, then pass that instead — every address,
balance, UTXO and history feature works watch-only:

```
derive_addresses(source="zpub6rFR7y4Q2Aij...", count=20)
```

If you do enable seed-phrase tools, use a testnet or throwaway seed. See
[SECURITY.md](SECURITY.md).

---

## What it does

| | |
|---|---|
| **Derivation** | BIP-32/39/44/49/84/86/85 — Legacy, Nested SegWit, Native SegWit and Taproot, verified against the spec vectors |
| **Watch-only** | Full address/balance/UTXO/history from an xpub, ypub or zpub |
| **Chain queries** | Balances, UTXOs, paginated history, transactions, blocks, scripts |
| **Fees & mempool** | Smart estimates, recommended tiers, fee histogram, mempool stats |
| **PSBT inspection** | Decode a PSBT with destinations, fee and warnings before you sign elsewhere |
| **Backends** | Bitcoin Core, Electrum, mempool.space, Esplora — with priority failover |
| **Networks** | mainnet, testnet, signet, regtest |
| **Privacy** | Optional Tor routing, enforced fail-closed |

**It cannot build, sign or broadcast transactions.** That is deliberate — see
[ROADMAP.md](ROADMAP.md) for why, and what replaces it.

---

## Install

Not yet on PyPI. Install from source:

```bash
git clone https://github.com/junxit/mcp-bitcoin
cd mcp-bitcoin
uv sync
```

Confirm it works:

```bash
uv run mcp-bitcoin --help 2>/dev/null || echo "installed"
uv run pytest -q
```

---

## Configure

Add a block to your MCP client config. For Claude Desktop that is
`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS;
for Claude Code, `.mcp.json` in your project or `~/.claude.json`.

Replace `/ABSOLUTE/PATH/TO/mcp-bitcoin` with where you cloned it.

### Public APIs — no node required

The quickest way to start. Uses mempool.space with Blockstream as a fallback.

```json
{
  "mcpServers": {
    "bitcoin": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_NETWORK": "mainnet",
        "MCP_BITCOIN_MEMPOOL_URL": "https://mempool.space/api",
        "MCP_BITCOIN_ESPLORA_URL": "https://blockstream.info/api",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "mempool,esplora"
      }
    }
  }
}
```

> Public APIs see every address you look up. If that matters, self-host or use Tor.

### Testnet

```json
{
  "mcpServers": {
    "bitcoin-testnet": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_NETWORK": "testnet",
        "MCP_BITCOIN_MEMPOOL_URL": "https://mempool.space/testnet4/api",
        "MCP_BITCOIN_ESPLORA_URL": "https://blockstream.info/testnet/api",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "mempool,esplora",
        "MCP_BITCOIN_ALLOW_MNEMONIC": "true"
      }
    }
  }
}
```

Enabling seed-phrase tools is reasonable here — testnet coins are worthless.

### Your own Bitcoin Core

Omit credentials from the URL and the server reads `~/.bitcoin/.cookie`.

```json
{
  "mcpServers": {
    "bitcoin": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_CORE_URL": "http://127.0.0.1:8332",
        "MCP_BITCOIN_ELECTRUM_URL": "ssl://192.168.1.50:50002",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "core,electrum"
      }
    }
  }
}
```

Core has no address index, so pair it with Electrum, mempool.space or Esplora
for `get_balance`, `get_utxos` and `get_tx_history`. The server routes each
call to a backend that supports it.

### Self-hosted over Tor

```json
{
  "mcpServers": {
    "bitcoin": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_TOR_PROXY": "socks5://127.0.0.1:9050",
        "MCP_BITCOIN_CORE_URL": "http://yournode.onion:8332",
        "MCP_BITCOIN_ELECTRUM_URL": "ssl://yourelectrum.onion:50002",
        "MCP_BITCOIN_MEMPOOL_URL": "http://yourmempool.onion/api",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "core,electrum,mempool"
      }
    }
  }
}
```

With `MCP_BITCOIN_TOR_PROXY` set, every backend connection routes through the
proxy and the server **refuses to start** if it is unreachable. `socks5h://` is
accepted and normalized. Certificate verification is skipped for `.onion` hosts
because the onion address already authenticates the endpoint.

### Public mempool.space over Tor

```json
{
  "mcpServers": {
    "bitcoin": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_TOR_PROXY": "socks5://127.0.0.1:9050",
        "MCP_BITCOIN_MEMPOOL_URL": "http://mempoolhqx4isw62xs7abwphsq7ldayuidyx2v2oethdhhj6mlo2r6ad.onion/api",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "mempool"
      }
    }
  }
}
```

### Regtest

```json
{
  "mcpServers": {
    "bitcoin-regtest": {
      "command": "uv",
      "args": ["run", "--directory", "/ABSOLUTE/PATH/TO/mcp-bitcoin", "mcp-bitcoin"],
      "env": {
        "MCP_BITCOIN_NETWORK": "regtest",
        "MCP_BITCOIN_CORE_URL": "http://127.0.0.1:18443",
        "MCP_BITCOIN_PROVIDER_PRIORITY": "core",
        "MCP_BITCOIN_ALLOW_MNEMONIC": "true"
      }
    }
  }
}
```

Start the node with `bitcoind -regtest -fallbackfee=0.0001`. Derived addresses
correctly use the `bcrt1` prefix.

---

## Example session

```
You:  What's the current Bitcoin fee rate, and how busy is the mempool?

Claude: [get_recommended_fees] [get_mempool_info]
        Fees are low right now — economy and normal are both 1 sat/vB,
        priority is 3. The mempool holds 82,735 transactions (41.6 MvB),
        so there's no meaningful backlog. A 1-2 sat/vB transaction should
        confirm within a few blocks.

You:  Show me the first 3 addresses for this account key: zpub6rFR7y4Q2Aij...

Claude: [derive_addresses]
        m/84'/0'/0'/0/0  bc1qcr8te4kr609gcawutmrza0j4xv80jy8z306fyu
        m/84'/0'/0'/0/1  bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g
        m/84'/0'/0'/0/2  bc1qp59yckz4ae5c4efgw2s5wfyvrz0ala7rgvuz8z

You:  Any balance on the first one?

Claude: [get_balance] [get_utxos]
        0.00000000 BTC — no UTXOs, and no transaction history.
```

---

## Tools

Tools marked **offline** work with no backend configured.

### Derivation
| Tool | | |
|---|---|---|
| `derive_addresses` | offline | Addresses from an xpub or seed phrase |
| `derive_from_path` | offline | One key at any BIP-32 path, all four formats |
| `derive_xpub` | offline · 🔑 | Account xpub/ypub/zpub from a seed phrase |
| `derive_entropy_bip85` | offline · 🔑 | BIP-85 child seed or entropy |
| `generate_mnemonic` | offline · 🔑 | New BIP-39 phrase |
| `validate_mnemonic` | offline | Check word count, wordlist and checksum |
| `decode_wif` | offline · 🔑 | Public key and addresses for a WIF key |
| `get_address_from_pubkey` | offline | Hex public key to address |

🔑 = requires `MCP_BITCOIN_ALLOW_MNEMONIC=true`

### Chain queries
| Tool | | |
|---|---|---|
| `get_balance` | backend | Confirmed and unconfirmed balance |
| `get_utxos` | backend | Unspent outputs, filterable by confirmations |
| `get_tx_history` | backend | History, newest first, cursor-paginated |
| `get_transaction` | backend | Fetch and decode a transaction |
| `get_raw_transaction` | backend | Raw hex |
| `get_block` | backend | Block header by hash or height |
| `get_block_height` | backend | Current chain tip |
| `decode_raw_transaction` | offline | Decode raw hex with destinations |
| `decode_psbt` | offline | Decode a PSBT with destinations and fee |
| `validate_address` | offline | Validity, type and network |
| `decode_script` | offline | Script type and address |

### Fees and mempool
| Tool | | |
|---|---|---|
| `estimate_fee` | backend | Rate for a confirmation target |
| `get_recommended_fees` | backend | Economy / normal / priority |
| `get_fee_histogram` | backend | Mempool fee distribution |
| `get_mempool_info` | backend | Size, vsize, minimum rate |
| `get_mempool_entry` | backend | Details for an unconfirmed transaction |

### Utility
| Tool | | |
|---|---|---|
| `ping` | | Health and backend reachability |
| `list_backends` | | Backends, status and capabilities |
| `convert_units` | offline | BTC / mBTC / bits / sats |

---

## Configuration reference

| Variable | Default | |
|---|---|---|
| `MCP_BITCOIN_NETWORK` | `mainnet` | `mainnet`, `testnet`, `signet`, `regtest` |
| `MCP_BITCOIN_CORE_URL` | — | Bitcoin Core RPC; omit credentials to use cookie auth |
| `MCP_BITCOIN_CORE_COOKIE_PATH` | auto | Override the `.cookie` location |
| `MCP_BITCOIN_ELECTRUM_URL` | — | `ssl://host:50002` or `tcp://host:50001` |
| `MCP_BITCOIN_ELECTRUM_ALLOW_SELF_SIGNED` | `false` | Accept self-signed certs (unauthenticated) |
| `MCP_BITCOIN_MEMPOOL_URL` | — | mempool.space API base |
| `MCP_BITCOIN_ESPLORA_URL` | — | Esplora API base |
| `MCP_BITCOIN_PROVIDER_PRIORITY` | `core,electrum,mempool,esplora` | Failover order |
| `MCP_BITCOIN_TOR_PROXY` | — | SOCKS5 proxy; fail-closed when set |
| `MCP_BITCOIN_ALLOW_MNEMONIC` | `false` | Enable seed-phrase and private-key tools |
| `MCP_BITCOIN_TIMEOUT` | `30` | Per-backend request timeout, seconds |
| `MCP_BITCOIN_FAILOVER_COOLDOWN` | `60` | Initial backoff for a failed backend, seconds |

---

## Troubleshooting

**"No backend is configured"** — set at least one `*_URL` variable. Derivation,
validation and decoding still work without one.

**"Cannot run 'get_balance' … does not support this operation"** — Bitcoin Core
has no address index. Add an Electrum, mempool.space or Esplora backend.

**Backend errors** — run `ping` for per-backend reachability and latency, and
`list_backends` to see which capabilities each one provides.

**"Address … is a mainnet address, but this server is configured for testnet"** —
working as intended. Querying across networks returns another chain's data,
which looks like missing funds. Change `MCP_BITCOIN_NETWORK` or use the right
address.

**Electrum TLS failures** — many Electrum servers use self-signed certificates.
For a server you trust, set `MCP_BITCOIN_ELECTRUM_ALLOW_SELF_SIGNED=true`, and
read the caveat in [SECURITY.md](SECURITY.md).

**Server won't start with Tor enabled** — that is the fail-closed guarantee.
Start Tor, or unset `MCP_BITCOIN_TOR_PROXY`.

---

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check . && uv run mypy src/
```

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: assert against
published spec vectors, not against this implementation.

---

## License

[Apache-2.0](LICENSE) © 2026 Jade Naaman.

Provided without warranty. You are responsible for verifying anything this tool
tells you before acting on it.
