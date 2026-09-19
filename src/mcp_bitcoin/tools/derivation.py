"""Derivation tool handlers."""

from __future__ import annotations

from typing import Any

from embit import ec

from mcp_bitcoin.config import Network, ServerConfig
from mcp_bitcoin.derivation.addresses import (
    VALID_ADDRESS_TYPES,
    pubkey_to_address,
    pubkey_to_p2pkh,
    pubkey_to_p2sh_p2wpkh,
    pubkey_to_p2tr,
    pubkey_to_p2wpkh,
)
from mcp_bitcoin.derivation.bip85 import APP_HEX, derive_bip39_mnemonic, derive_entropy
from mcp_bitcoin.derivation.hd import (
    STANDARD_VERSION_KEYS,
    derive_key,
    key_from_xpub,
    master_key_from_seed,
    xpub_from_key,
)
from mcp_bitcoin.derivation.mnemonic import (
    VALID_WORD_COUNTS,
    mnemonic_to_seed,
)
from mcp_bitcoin.derivation.mnemonic import (
    generate_mnemonic as _generate,
)
from mcp_bitcoin.derivation.mnemonic import (
    validate_mnemonic as _validate,
)
from mcp_bitcoin.derivation.standards import (
    build_account_path,
    derive_addresses_from_xpub,
    standard_for_address_type,
)
from mcp_bitcoin.derivation.standards import (
    derive_addresses as _derive_addresses,
)
from mcp_bitcoin.derivation.wif import decode_wif as _decode_wif
from mcp_bitcoin.types import model_list

XPUB_PREFIXES = ("xpub", "ypub", "zpub", "tpub", "upub", "vpub", "Ypub", "Zpub")

SECRET_REFUSAL = (
    "This tool needs a secret (a seed phrase or private key), and the server is "
    "running in watch-only mode.\n\n"
    "Anything you pass here travels through the model's context window and, with "
    "a hosted client, a cloud API and your saved chat history. That is a poor "
    "place for a key that controls real funds.\n\n"
    "Safer alternatives:\n"
    "  * Derive an xpub/ypub/zpub once on an offline machine and pass that "
    "instead — every address query works watch-only.\n"
    "  * Use a testnet or throwaway seed.\n\n"
    "If you accept the exposure, set MCP_BITCOIN_ALLOW_MNEMONIC=true in the "
    "server's environment and restart it."
)


class SecretsNotAllowedError(Exception):
    """Raised when a secret-accepting tool is used in watch-only mode."""

    def __init__(self) -> None:
        super().__init__(SECRET_REFUSAL)


def require_secrets_allowed(config: ServerConfig) -> None:
    """Gate tools that accept key material behind an explicit opt-in."""
    if not config.allow_mnemonic:
        raise SecretsNotAllowedError()


def looks_like_xpub(source: str) -> bool:
    return source.strip().startswith(XPUB_PREFIXES)


def _pubkey_of(key: Any) -> ec.PublicKey:
    return key.to_public().key if key.is_private else key.key


async def handle_generate_mnemonic(config: ServerConfig, word_count: int = 24) -> dict[str, Any]:
    """Generate a new BIP-39 mnemonic."""
    require_secrets_allowed(config)
    if word_count not in VALID_WORD_COUNTS:
        raise ValueError(f"Invalid word count: {word_count}. Valid: {list(VALID_WORD_COUNTS)}")
    return {
        "mnemonic": _generate(word_count),
        "word_count": word_count,
        "warning": (
            "This phrase has now passed through the model's context and your "
            "client's transcript. Treat it as compromised for anything holding "
            "real value — generate production seeds on an offline device."
        ),
    }


async def handle_validate_mnemonic(mnemonic: str) -> dict[str, Any]:
    """Validate a BIP-39 mnemonic's word count, wordlist and checksum."""
    return _validate(mnemonic)


async def handle_derive_addresses(
    config: ServerConfig,
    source: str,
    passphrase: str = "",
    address_type: str = "native_segwit",
    account: int = 0,
    change: int = 0,
    start_index: int = 0,
    count: int = 10,
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    """Derive addresses from a mnemonic or an extended public key."""
    source = (source or "").strip()
    if not source:
        raise ValueError("source is required: a BIP-39 mnemonic or an xpub/ypub/zpub")

    standard = standard_for_address_type(address_type)

    if looks_like_xpub(source):
        xpub_key = key_from_xpub(source)
        addresses = derive_addresses_from_xpub(
            xpub_key,
            address_type,
            network,
            change,
            start_index,
            count,
            account_path=build_account_path(standard, network, account),
        )
        return {
            "source_type": "xpub",
            "standard": standard,
            "address_type": address_type,
            "network": network.value,
            "addresses": model_list(addresses),
            "note": (
                "Paths assume this key sits at the standard account level. If it "
                "was exported from a different path, the indices are still "
                "correct but the prefix shown may not be."
            ),
        }

    require_secrets_allowed(config)
    master = master_key_from_seed(mnemonic_to_seed(source, passphrase))
    addresses = _derive_addresses(master, standard, network, account, change, start_index, count)
    return {
        "source_type": "mnemonic",
        "standard": standard,
        "address_type": address_type,
        "network": network.value,
        "passphrase_used": bool(passphrase),
        "addresses": model_list(addresses),
    }


async def handle_derive_from_path(
    config: ServerConfig,
    source: str,
    path: str,
    passphrase: str = "",
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    """Derive a key at an arbitrary BIP-32 path and show every address encoding."""
    source = (source or "").strip()
    if not source:
        raise ValueError("source is required: a BIP-39 mnemonic or an xpub/ypub/zpub")

    if looks_like_xpub(source):
        parent = key_from_xpub(source)
        source_type = "xpub"
    else:
        require_secrets_allowed(config)
        parent = master_key_from_seed(mnemonic_to_seed(source, passphrase))
        source_type = "mnemonic"

    child = derive_key(parent, path)
    pubkey = _pubkey_of(child)

    return {
        "source_type": source_type,
        "path": path,
        "network": network.value,
        "public_key": pubkey.sec().hex(),
        "addresses": {
            "legacy": pubkey_to_p2pkh(pubkey, network),
            "nested_segwit": pubkey_to_p2sh_p2wpkh(pubkey, network),
            "native_segwit": pubkey_to_p2wpkh(pubkey, network),
            "taproot": pubkey_to_p2tr(pubkey, network),
        },
        "note": (
            "Private keys are not returned. The addresses and public key are "
            "everything needed to watch this path."
        ),
    }


async def handle_derive_xpub(
    config: ServerConfig,
    mnemonic: str,
    standard: str = "bip84",
    passphrase: str = "",
    account: int = 0,
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    """Extract the account-level extended public key for a standard."""
    require_secrets_allowed(config)
    if standard not in STANDARD_VERSION_KEYS:
        raise ValueError(f"Unknown standard: {standard}. Valid: {', '.join(STANDARD_VERSION_KEYS)}")

    master = master_key_from_seed(mnemonic_to_seed(mnemonic, passphrase))
    account_path = build_account_path(standard, network, account)
    account_key = derive_key(master, account_path)

    return {
        "xpub": xpub_from_key(account_key, standard, network),
        "standard": standard,
        "path": account_path,
        "account": account,
        "network": network.value,
        "note": (
            "Keep this xpub for watch-only use: pass it to derive_addresses and "
            "the server never needs the seed phrase again."
        ),
    }


async def handle_derive_entropy_bip85(
    config: ServerConfig,
    mnemonic: str,
    index: int = 0,
    passphrase: str = "",
    child_mnemonic_words: int | None = None,
    num_bytes: int = 64,
) -> dict[str, Any]:
    """Derive deterministic child entropy or a child mnemonic per BIP-85."""
    require_secrets_allowed(config)
    master = master_key_from_seed(mnemonic_to_seed(mnemonic, passphrase))

    if child_mnemonic_words:
        child = derive_bip39_mnemonic(master, child_mnemonic_words, index)
        return {
            "application": "bip39",
            "path": f"m/83696968'/39'/0'/{child_mnemonic_words}'/{index}'",
            "index": index,
            "child_word_count": child_mnemonic_words,
            "child_mnemonic": child,
            "warning": (
                "This child seed has passed through the model's context and your "
                "client's transcript. Treat it as compromised for real funds."
            ),
        }

    entropy = derive_entropy(master, APP_HEX, index, num_bytes)
    return {
        "application": "hex",
        "path": f"m/83696968'/{APP_HEX}'/{num_bytes}'/{index}'",
        "index": index,
        "num_bytes": num_bytes,
        "entropy_hex": entropy.hex(),
        "note": (
            "num_bytes is part of the derivation path, so requesting a different "
            "length yields entirely different entropy, not a truncation."
        ),
    }


async def handle_decode_wif(
    config: ServerConfig, wif: str, network: Network = Network.MAINNET
) -> dict[str, Any]:
    """Decode a WIF private key into its public components."""
    require_secrets_allowed(config)
    return _decode_wif(wif, network)


async def handle_get_address_from_pubkey(
    pubkey_hex: str,
    address_type: str = "native_segwit",
    network: Network = Network.MAINNET,
) -> dict[str, Any]:
    """Convert a hex public key to an address."""
    try:
        pubkey = ec.PublicKey.parse(bytes.fromhex(pubkey_hex.strip()))
    except Exception as exc:
        raise ValueError(f"Invalid public key {pubkey_hex!r}: {exc}") from exc

    if address_type.lower() == "all":
        return {
            "public_key": pubkey.sec().hex(),
            "network": network.value,
            "addresses": {t: pubkey_to_address(pubkey, t, network) for t in VALID_ADDRESS_TYPES},
        }

    return {
        "public_key": pubkey.sec().hex(),
        "address": pubkey_to_address(pubkey, address_type, network),
        "address_type": address_type,
        "network": network.value,
    }
