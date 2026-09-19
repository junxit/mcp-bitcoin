"""BIP-85 deterministic entropy derivation from a master seed.

Reference: https://github.com/bitcoin/bips/blob/master/bip-0085.mediawiki

Every BIP-85 application hangs off purpose ``83696968'``, but the *shape* of the
path below that differs per application — the number of bytes requested is itself
a path component for the HEX application, not a truncation parameter. Getting
this wrong produces entropy that no other BIP-85 implementation will reproduce,
which for a derived wallet means unrecoverable funds.
"""

from __future__ import annotations

import hashlib
import hmac

from embit import bip32, bip39

from mcp_bitcoin.derivation.hd import derive_key

BIP85_PURPOSE = 83696968
HMAC_KEY = b"bip-entropy-from-k"

APP_BIP39 = 39
APP_HEX = 128169
APP_WIF = 2
APP_XPRV = 32

LANGUAGE_ENGLISH = 0

MIN_HEX_BYTES = 16
MAX_HEX_BYTES = 64


def _entropy_from_path(master_key: bip32.HDKey, path: str) -> bytes:
    """Derive at ``path`` and run the BIP-85 HMAC-SHA512 over the private key.

    The check happens before derivation: every BIP-85 path is fully hardened, so
    a public key would otherwise fail inside embit with a message that doesn't
    explain what the caller did wrong.
    """
    if not master_key.is_private:
        raise ValueError(
            "BIP-85 requires a private master key. Extended public keys cannot "
            "derive entropy, because every BIP-85 path is hardened."
        )
    child = derive_key(master_key, path)
    return hmac.new(HMAC_KEY, child.key.serialize(), hashlib.sha512).digest()


def derive_entropy(
    master_key: bip32.HDKey,
    app_no: int,
    index: int = 0,
    num_bytes: int = 64,
) -> bytes:
    """Derive raw entropy per the BIP-85 HEX application.

    Path: ``m/83696968'/{app_no}'/{num_bytes}'/{index}'``

    Args:
        master_key: BIP-32 master key; must be private.
        app_no: Application number (``128169`` for HEX).
        index: Derivation index.
        num_bytes: Entropy length, 16-64. This is part of the derivation path,
            so different lengths yield entirely different entropy.

    Returns:
        ``num_bytes`` of derived entropy.

    Raises:
        ValueError: If ``num_bytes`` is out of range or the key is not private.
    """
    if not MIN_HEX_BYTES <= num_bytes <= MAX_HEX_BYTES:
        raise ValueError(
            f"num_bytes must be between {MIN_HEX_BYTES} and {MAX_HEX_BYTES}, got {num_bytes}"
        )
    if index < 0:
        raise ValueError(f"index must be non-negative, got {index}")

    path = f"m/{BIP85_PURPOSE}'/{app_no}'/{num_bytes}'/{index}'"
    return _entropy_from_path(master_key, path)[:num_bytes]


def derive_bip39_mnemonic(
    master_key: bip32.HDKey,
    word_count: int = 24,
    index: int = 0,
    language: int = LANGUAGE_ENGLISH,
) -> str:
    """Derive a child BIP-39 mnemonic per the BIP-85 BIP-39 application.

    Path: ``m/83696968'/39'/{language}'/{word_count}'/{index}'``

    Args:
        master_key: BIP-32 master key; must be private.
        word_count: 12, 15, 18, 21 or 24.
        index: Derivation index.
        language: Wordlist index; only 0 (English) is supported.

    Returns:
        The derived mnemonic phrase.

    Raises:
        ValueError: On an unsupported word count or language.
    """
    from mcp_bitcoin.derivation.mnemonic import WORD_COUNT_TO_ENTROPY

    if word_count not in WORD_COUNT_TO_ENTROPY:
        raise ValueError(
            f"Invalid word count: {word_count}. Valid: {sorted(WORD_COUNT_TO_ENTROPY)}"
        )
    if language != LANGUAGE_ENGLISH:
        raise ValueError("Only the English wordlist (language=0) is supported")
    if index < 0:
        raise ValueError(f"index must be non-negative, got {index}")

    entropy_bytes = WORD_COUNT_TO_ENTROPY[word_count] // 8
    path = f"m/{BIP85_PURPOSE}'/{APP_BIP39}'/{language}'/{word_count}'/{index}'"
    entropy = _entropy_from_path(master_key, path)
    return str(bip39.mnemonic_from_bytes(entropy[:entropy_bytes]))
