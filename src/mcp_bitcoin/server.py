"""MCP server: tool registration and dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import socket
import sys
from collections.abc import Awaitable, Callable
from typing import Any, ParamSpec, TypeVar
from urllib.parse import urlparse

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from mcp_bitcoin import __version__
from mcp_bitcoin.config import Network, ProviderType, ServerConfig, load_config
from mcp_bitcoin.providers.base import (
    BackendUnavailableError,
    BitcoinProvider,
    NetworkMismatchError,
    ProviderManager,
)
from mcp_bitcoin.providers.core import BitcoinCoreProvider
from mcp_bitcoin.providers.electrum import ElectrumProvider
from mcp_bitcoin.providers.rest import EsploraProvider, MempoolProvider
from mcp_bitcoin.tools.derivation import SecretsNotAllowedError
from mcp_bitcoin.utils.sanitize import configure_logging

logger = logging.getLogger(__name__)

INSTRUCTIONS = """\
Read-only Bitcoin access: HD wallet derivation, address and UTXO queries, \
transaction lookup, fee estimation and mempool analysis.

This server cannot build, sign or broadcast transactions.

Seed phrases and private keys passed to these tools travel through your context \
window and, on a hosted client, a cloud API and saved chat history. Prefer the \
watch-only path: derive an xpub/ypub/zpub once, then pass that to \
derive_addresses. The server refuses secrets entirely unless the operator has \
set MCP_BITCOIN_ALLOW_MNEMONIC=true.\
"""

# Every tool in this server is read-only: nothing here can spend, sign or
# broadcast. Declaring that lets clients relax approval prompts accordingly.
READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

mcp = MCPServer(
    "mcp-bitcoin",
    version=__version__,
    instructions=INSTRUCTIONS,
)


P = ParamSpec("P")
CallableT = TypeVar("CallableT", bound=Callable[..., Any])


def tool() -> Callable[[CallableT], CallableT]:
    """Register a read-only MCP tool."""
    return mcp.tool(annotations=READ_ONLY)


_config: ServerConfig | None = None
_pm: ProviderManager | None = None


def configure(config: ServerConfig, pm: ProviderManager) -> None:
    """Install the server's configuration and provider manager."""
    global _config, _pm
    _config, _pm = config, pm


def get_config() -> ServerConfig:
    if _config is None:
        raise RuntimeError("Server not initialized")
    return _config


def get_pm() -> ProviderManager:
    if _pm is None:
        raise RuntimeError("Server not initialized")
    return _pm


def _network(override: str | None) -> Network:
    """Resolve a per-call network override against the configured default."""
    if not override:
        return get_config().network
    try:
        return Network(override.strip().lower())
    except ValueError as exc:
        valid = ", ".join(n.value for n in Network)
        raise ValueError(f"Unknown network {override!r}. Valid: {valid}") from exc


def _render(data: dict[str, Any], summary: str | None = None) -> str:
    """Render a result as a human summary plus the structured payload."""
    parts = []
    if summary:
        parts.extend([summary, ""])
    parts.extend(["```json", json.dumps(data, indent=2, default=str), "```"])
    return "\n".join(parts)


def _error(payload: dict[str, Any], summary: str) -> str:
    return _render(payload, f"❌ {summary}")


def tool_handler(fn: Callable[P, Awaitable[str]]) -> Callable[P, Awaitable[str]]:
    """Translate internal exceptions into actionable tool output.

    Without this, a backend outage surfaces to the model as an opaque traceback
    rather than something it can explain or route around.
    """

    @functools.wraps(fn)
    async def wrapper(*args: P.args, **kwargs: P.kwargs) -> str:
        try:
            return await fn(*args, **kwargs)
        except SecretsNotAllowedError as exc:
            return _error(
                {"error": "secrets_not_allowed", "message": str(exc)},
                "Refused: server is in watch-only mode.",
            )
        except NetworkMismatchError as exc:
            return _error(
                {"error": "network_mismatch", "message": exc.message},
                "Backend is serving a different chain than configured.",
            )
        except BackendUnavailableError as exc:
            return _error(exc.as_dict(), "No backend could answer that.")
        except ValueError as exc:
            return _error({"error": "invalid_input", "message": str(exc)}, "Invalid input.")
        except Exception as exc:  # pragma: no cover - last-resort guard
            logger.exception("Unhandled error in %s", fn.__name__)
            return _error(
                {"error": "internal_error", "message": f"{type(exc).__name__}: {exc}"},
                "Unexpected error.",
            )

    return wrapper


# =============================================================================
# Utility
# =============================================================================


@tool()
@tool_handler
async def ping() -> str:
    """Check server health and which backends are reachable.

    Start here when other tools report backend errors.
    """
    from mcp_bitcoin.tools.utility import handle_ping

    result = await handle_ping(get_pm(), get_config())
    return _render(
        result,
        f"Server OK on {result['network']} — {result['backends_reachable']} backends reachable.",
    )


@tool()
@tool_handler
async def list_backends() -> str:
    """List configured backends, their reachability and their capabilities."""
    from mcp_bitcoin.tools.utility import handle_list_backends

    return _render(await handle_list_backends(get_pm(), get_config()))


@tool()
@tool_handler
async def convert_units(amount: str, from_unit: str, to_unit: str) -> str:
    """Convert between Bitcoin units. Works offline.

    Args:
        amount: Amount to convert, e.g. "0.001".
        from_unit: Source unit: btc, mbtc, bit or sat.
        to_unit: Target unit: btc, mbtc, bit or sat.
    """
    from mcp_bitcoin.tools.utility import handle_convert_units

    result = await handle_convert_units(amount, from_unit, to_unit)
    out = result["output"]
    return _render(result, f"{amount} {from_unit} = {out['amount']} {out['unit']}")


# =============================================================================
# Derivation
# =============================================================================


@tool()
@tool_handler
async def generate_mnemonic(word_count: int = 24) -> str:
    """Generate a new BIP-39 seed phrase. Works offline.

    Requires MCP_BITCOIN_ALLOW_MNEMONIC=true, because the generated phrase is
    returned into the conversation and therefore into your client's transcript.
    Never use a phrase generated this way for real funds — use a hardware
    wallet or an offline generator.

    Args:
        word_count: 12, 15, 18, 21 or 24. Defaults to 24.
    """
    from mcp_bitcoin.tools.derivation import handle_generate_mnemonic

    result = await handle_generate_mnemonic(get_config(), word_count)
    return _render(result, f"Generated a {word_count}-word BIP-39 phrase.")


@tool()
@tool_handler
async def validate_mnemonic(mnemonic: str) -> str:
    """Check a BIP-39 phrase's word count, wordlist and checksum. Works offline.

    Args:
        mnemonic: The space-separated seed phrase to check.
    """
    from mcp_bitcoin.tools.derivation import handle_validate_mnemonic

    result = await handle_validate_mnemonic(mnemonic)
    summary = (
        f"Valid {result['word_count']}-word mnemonic."
        if result["valid"]
        else f"Invalid: {result.get('error')}"
    )
    return _render(result, summary)


@tool()
@tool_handler
async def derive_addresses(
    source: str,
    passphrase: str = "",
    address_type: str = "native_segwit",
    account: int = 0,
    change: int = 0,
    start_index: int = 0,
    count: int = 10,
    network: str | None = None,
) -> str:
    """Derive addresses from an xpub (preferred) or a seed phrase. Works offline.

    Passing an xpub/ypub/zpub is the safe path and needs no special permission.
    Passing a mnemonic requires MCP_BITCOIN_ALLOW_MNEMONIC=true.

    Args:
        source: An xpub/ypub/zpub, or a BIP-39 mnemonic.
        passphrase: Optional BIP-39 passphrase; only used with a mnemonic.
        address_type: legacy, nested_segwit, native_segwit or taproot.
        account: BIP-44 account index.
        change: 0 for receiving addresses, 1 for change.
        start_index: First address index.
        count: How many addresses to derive, up to 500.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.derivation import handle_derive_addresses

    result = await handle_derive_addresses(
        get_config(),
        source,
        passphrase,
        address_type,
        account,
        change,
        start_index,
        count,
        _network(network),
    )
    return _render(
        result,
        f"Derived {len(result['addresses'])} {address_type} addresses "
        f"from {result['source_type']}.",
    )


@tool()
@tool_handler
async def derive_from_path(
    source: str, path: str, passphrase: str = "", network: str | None = None
) -> str:
    """Derive one key at an arbitrary BIP-32 path, in every address format.

    Works offline. Private keys are never returned.

    Args:
        source: An xpub/ypub/zpub, or a BIP-39 mnemonic.
        path: A BIP-32 path such as "m/84'/0'/0'/0/0".
        passphrase: Optional BIP-39 passphrase; only used with a mnemonic.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.derivation import handle_derive_from_path

    result = await handle_derive_from_path(
        get_config(), source, path, passphrase, _network(network)
    )
    return _render(result, f"Derived {path}.")


@tool()
@tool_handler
async def derive_xpub(
    mnemonic: str,
    standard: str = "bip84",
    passphrase: str = "",
    account: int = 0,
    network: str | None = None,
) -> str:
    """Extract an account xpub/ypub/zpub from a seed phrase. Works offline.

    Requires MCP_BITCOIN_ALLOW_MNEMONIC=true. Run this once, keep the xpub, and
    every later address query can stay watch-only.

    Args:
        mnemonic: The BIP-39 seed phrase.
        standard: bip44, bip49, bip84 or bip86.
        passphrase: Optional BIP-39 passphrase.
        account: Account index.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.derivation import handle_derive_xpub

    result = await handle_derive_xpub(
        get_config(), mnemonic, standard, passphrase, account, _network(network)
    )
    return _render(result, f"Derived {standard} account key at {result['path']}.")


@tool()
@tool_handler
async def derive_entropy_bip85(
    mnemonic: str,
    index: int = 0,
    passphrase: str = "",
    child_mnemonic_words: int | None = None,
    num_bytes: int = 64,
) -> str:
    """Derive a deterministic child seed or entropy from a master seed (BIP-85).

    Works offline. Requires MCP_BITCOIN_ALLOW_MNEMONIC=true.

    Args:
        mnemonic: The master BIP-39 seed phrase.
        index: Derivation index; each index gives a different child.
        passphrase: Optional BIP-39 passphrase for the master seed.
        child_mnemonic_words: Set to 12/15/18/21/24 to derive a child seed
            phrase. Leave unset to derive raw hex entropy instead.
        num_bytes: Entropy length, 16-64, when deriving raw entropy.
    """
    from mcp_bitcoin.tools.derivation import handle_derive_entropy_bip85

    result = await handle_derive_entropy_bip85(
        get_config(), mnemonic, index, passphrase, child_mnemonic_words, num_bytes
    )
    summary = (
        f"Derived a {result['child_word_count']}-word child seed at index {index}."
        if "child_mnemonic" in result
        else f"Derived {result['num_bytes']} bytes of entropy at index {index}."
    )
    return _render(result, summary)


@tool()
@tool_handler
async def decode_wif(wif: str, network: str | None = None) -> str:
    """Show the public key and addresses for a WIF private key. Works offline.

    Requires MCP_BITCOIN_ALLOW_MNEMONIC=true. The private key is not echoed back.

    Args:
        wif: The WIF-encoded private key.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.derivation import handle_decode_wif

    result = await handle_decode_wif(get_config(), wif, _network(network))
    return _render(result, f"Decoded a {result['network']} WIF key.")


@tool()
@tool_handler
async def get_address_from_pubkey(
    pubkey: str, address_type: str = "native_segwit", network: str | None = None
) -> str:
    """Convert a hex public key to an address. Works offline.

    Args:
        pubkey: Hex-encoded public key, compressed or uncompressed.
        address_type: legacy, nested_segwit, native_segwit, taproot, or "all".
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.derivation import handle_get_address_from_pubkey

    result = await handle_get_address_from_pubkey(pubkey, address_type, _network(network))
    return _render(result, result.get("address", "Derived all address formats."))


# =============================================================================
# Chain queries
# =============================================================================


@tool()
@tool_handler
async def get_balance(address: str, network: str | None = None) -> str:
    """Get an address's confirmed and unconfirmed balance. Needs a backend.

    Args:
        address: The Bitcoin address to look up.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_balance

    result = await handle_get_balance(get_pm(), address, _network(network))
    return _render(result, f"{result['total_btc']} BTC ({result['total_sats']:,} sats)")


@tool()
@tool_handler
async def get_utxos(address: str, min_confirmations: int = 0, network: str | None = None) -> str:
    """List an address's unspent outputs. Needs a backend.

    Args:
        address: The Bitcoin address to look up.
        min_confirmations: Exclude UTXOs below this confirmation count.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_utxos

    result = await handle_get_utxos(get_pm(), address, min_confirmations, _network(network))
    return _render(
        result,
        f"{result['utxo_count']} UTXOs totaling {result['total_value_btc']} BTC.",
    )


@tool()
@tool_handler
async def get_tx_history(
    address: str,
    limit: int = 25,
    after_txid: str | None = None,
    network: str | None = None,
) -> str:
    """Get an address's transaction history, newest first. Needs a backend.

    Paginates by cursor: pass the returned next_cursor as after_txid to page on.

    Args:
        address: The Bitcoin address to look up.
        limit: How many transactions to return, up to 100.
        after_txid: Continue after this txid, from a previous next_cursor.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_tx_history

    result = await handle_get_tx_history(get_pm(), address, limit, after_txid, _network(network))
    more = " (more available)" if result["has_more"] else ""
    return _render(result, f"{result['returned']} transactions{more}.")


@tool()
@tool_handler
async def get_transaction(txid: str, network: str | None = None) -> str:
    """Fetch and decode a transaction by txid. Needs a backend.

    Args:
        txid: The 64-hex-character transaction ID.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_transaction

    result = await handle_get_transaction(get_pm(), txid, _network(network))
    confs = result.get("confirmations")
    state = f"{confs:,} confirmations" if confs else "unconfirmed"
    return _render(result, f"Transaction {txid[:12]}… — {state}.")


@tool()
@tool_handler
async def get_raw_transaction(txid: str, network: str | None = None) -> str:
    """Fetch a transaction's raw hex. Needs a backend.

    Args:
        txid: The 64-hex-character transaction ID.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_raw_transaction

    result = await handle_get_raw_transaction(get_pm(), txid, _network(network))
    return _render(result, f"Raw transaction, {result['size_bytes']} bytes.")


@tool()
@tool_handler
async def decode_raw_transaction(raw_tx: str, network: str | None = None) -> str:
    """Decode raw transaction hex, showing destinations. Works offline.

    Args:
        raw_tx: The raw transaction hex.
        network: Override the server's network for address rendering.
    """
    from mcp_bitcoin.tools.query import handle_decode_raw_transaction

    result = await handle_decode_raw_transaction(raw_tx, _network(network))
    return _render(
        result,
        f"{result['input_count']} inputs, {result['output_count']} outputs, "
        f"{result['total_output_btc']} BTC out.",
    )


@tool()
@tool_handler
async def get_block(block: str, network: str | None = None) -> str:
    """Fetch a block header by hash or height. Needs a backend.

    Args:
        block: A block hash, or a block height as a number.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_block

    result = await handle_get_block(get_pm(), block, _network(network))
    return _render(result, f"Block {result['height']:,} ({result['hash'][:16]}…)")


@tool()
@tool_handler
async def get_block_height(network: str | None = None) -> str:
    """Get the current chain tip height. Needs a backend.

    Args:
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.query import handle_get_block_height

    result = await handle_get_block_height(get_pm(), _network(network))
    return _render(result, f"Chain tip: block {result['height']:,}")


@tool()
@tool_handler
async def validate_address(address: str, network: str | None = None) -> str:
    """Check whether an address is valid and which network it belongs to.

    Works offline.

    Args:
        address: The address to check.
        network: Compare against this network instead of the server default.
    """
    from mcp_bitcoin.tools.query import handle_validate_address

    result = await handle_validate_address(address, _network(network))
    if not result["valid"]:
        return _render(result, f"Not a valid address: {result.get('error')}")
    nets = "/".join(result.get("networks", []))
    return _render(result, f"Valid {result['address_type']} address ({nets}).")


@tool()
@tool_handler
async def decode_script(script_hex: str, network: str | None = None) -> str:
    """Decode a scriptPubKey to its type and address. Works offline.

    Args:
        script_hex: The hex-encoded script.
        network: Override the server's network for address rendering.
    """
    from mcp_bitcoin.tools.query import handle_decode_script

    result = await handle_decode_script(script_hex, _network(network))
    return _render(result, f"{result['script_type']} script ({result['size']} bytes).")


@tool()
@tool_handler
async def decode_psbt(psbt: str, network: str | None = None) -> str:
    """Decode a PSBT, showing every destination, the fee and signing status.

    Works offline. Use this to check where funds are going before signing in
    your wallet. This server cannot sign or broadcast.

    Args:
        psbt: The base64-encoded PSBT.
        network: Override the server's network for address rendering.
    """
    from mcp_bitcoin.tools.psbt import handle_decode_psbt

    result = await handle_decode_psbt(psbt, _network(network))
    fee = f", fee {result['fee_sats']:,} sats" if result.get("fee_sats") else ""
    summary = f"PSBT: {result['input_count']} inputs → {result['output_count']} outputs{fee}."
    if result.get("warnings"):
        summary += f" ⚠️  {len(result['warnings'])} warning(s)."
    return _render(result, summary)


# =============================================================================
# Fees and mempool
# =============================================================================


@tool()
@tool_handler
async def estimate_fee(target_blocks: int = 6, network: str | None = None) -> str:
    """Estimate the fee rate to confirm within a number of blocks. Needs a backend.

    Args:
        target_blocks: Desired confirmation target, 1 to 1008.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.fees import handle_estimate_fee

    result = await handle_estimate_fee(get_pm(), target_blocks, _network(network))
    return _render(
        result,
        f"~{result['sat_per_vbyte']} sat/vB for confirmation within {target_blocks} block(s).",
    )


@tool()
@tool_handler
async def get_recommended_fees(network: str | None = None) -> str:
    """Get economy, normal and priority fee rates. Needs a backend.

    Args:
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.fees import handle_get_recommended_fees

    result = await handle_get_recommended_fees(get_pm(), _network(network))
    return _render(
        result,
        f"Economy {result['economy']} · Normal {result['normal']} · "
        f"Priority {result['priority']} sat/vB",
    )


@tool()
@tool_handler
async def get_fee_histogram(network: str | None = None) -> str:
    """Get the mempool fee distribution. Needs a backend.

    Args:
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.fees import handle_get_fee_histogram

    result = await handle_get_fee_histogram(get_pm(), _network(network))
    return _render(result, f"Fee histogram: {result['entries']} buckets.")


@tool()
@tool_handler
async def get_mempool_info(network: str | None = None) -> str:
    """Get mempool size, vsize and minimum fee rate. Needs a backend.

    Args:
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.fees import handle_get_mempool_info

    result = await handle_get_mempool_info(get_pm(), _network(network))
    return _render(
        result,
        f"{result['tx_count']:,} transactions, {result['vsize']:,} vbytes.",
    )


@tool()
@tool_handler
async def get_mempool_entry(txid: str, network: str | None = None) -> str:
    """Get mempool details for an unconfirmed transaction. Needs a backend.

    Args:
        txid: The 64-hex-character transaction ID.
        network: Override the server's network for this call.
    """
    from mcp_bitcoin.tools.fees import handle_get_mempool_entry

    result = await handle_get_mempool_entry(get_pm(), txid, _network(network))
    rate = result.get("fee_rate_sat_vb")
    return _render(result, f"In mempool at {rate} sat/vB." if rate else "In mempool.")


# =============================================================================
# Startup
# =============================================================================


def check_tor_proxy(proxy_url: str, timeout: float = 5.0) -> None:
    """Verify the SOCKS5 proxy accepts connections, or exit.

    Tor is advertised as fail-closed, so a misconfigured proxy must stop the
    server rather than let every request fail one at a time with an obscure
    error.
    """
    parsed = urlparse(proxy_url)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        logger.error(
            "MCP_BITCOIN_TOR_PROXY=%r is missing a host or port. Expected "
            "something like socks5://127.0.0.1:9050",
            proxy_url,
        )
        sys.exit(1)

    try:
        with socket.create_connection((host, port), timeout=timeout):
            logger.info("Tor SOCKS5 proxy reachable at %s:%s", host, port)
    except OSError as exc:
        logger.error(
            "Tor is enabled but the SOCKS5 proxy at %s:%s is unreachable (%s).\n"
            "Refusing to start: continuing would send Bitcoin queries over "
            "clearnet or fail every request.\n"
            "Start Tor, or unset MCP_BITCOIN_TOR_PROXY.",
            host,
            port,
            exc,
        )
        sys.exit(1)


def build_provider_manager(config: ServerConfig) -> ProviderManager:
    """Construct and register providers from configuration."""
    pm = ProviderManager(config)
    tor = config.tor_proxy if config.tor_enabled else None

    builders: dict[ProviderType, Callable[[], BitcoinProvider]] = {
        ProviderType.CORE: lambda: BitcoinCoreProvider(
            str(config.core_url), config.network, tor, config.core_cookie_path
        ),
        ProviderType.ELECTRUM: lambda: ElectrumProvider(
            str(config.electrum_url),
            config.network,
            tor,
            config.electrum_allow_self_signed,
        ),
        ProviderType.MEMPOOL: lambda: MempoolProvider(str(config.mempool_url), config.network, tor),
        ProviderType.ESPLORA: lambda: EsploraProvider(str(config.esplora_url), config.network, tor),
    }

    for ptype in config.configured_providers():
        try:
            pm.register(builders[ptype]())
        except Exception as exc:
            logger.error("Could not configure the %s backend: %s", ptype.value, exc)

    return pm


def main() -> None:
    """Entry point for the MCP server."""
    configure_logging()
    config = load_config()

    if config.tor_enabled:
        check_tor_proxy(config.tor_proxy or "")

    pm = build_provider_manager(config)
    configure(config, pm)

    configured = config.configured_providers()
    if configured:
        logger.info(
            "mcp-bitcoin starting on %s with backends: %s",
            config.network.value,
            ", ".join(p.value for p in configured),
        )
    else:
        logger.warning(
            "No backend configured — only offline tools (derivation, validation, "
            "decoding, unit conversion) will work."
        )

    if not config.allow_mnemonic:
        logger.info(
            "Watch-only mode: tools that accept seed phrases or private keys are "
            "disabled. Set MCP_BITCOIN_ALLOW_MNEMONIC=true to enable them."
        )

    try:
        mcp.run(transport="stdio")
    finally:
        # Best-effort cleanup: the process is exiting either way.
        with contextlib.suppress(Exception):
            asyncio.run(pm.close_all())
