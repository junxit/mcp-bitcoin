"""BIP-39 mnemonic generation and validation."""

from __future__ import annotations

import secrets
from typing import Any

from embit import bip39

VALID_WORD_COUNTS = (12, 15, 18, 21, 24)

WORD_COUNT_TO_ENTROPY = {
    12: 128,
    15: 160,
    18: 192,
    21: 224,
    24: 256,
}


def generate_mnemonic(word_count: int = 24) -> str:
    """Generate a new BIP-39 mnemonic from cryptographically secure entropy.

    Args:
        word_count: 12, 15, 18, 21 or 24.

    Returns:
        The space-separated mnemonic phrase.

    Raises:
        ValueError: If ``word_count`` is not a valid BIP-39 length.
    """
    if word_count not in VALID_WORD_COUNTS:
        raise ValueError(f"Invalid word count: {word_count}. Valid: {list(VALID_WORD_COUNTS)}")
    entropy = secrets.token_bytes(WORD_COUNT_TO_ENTROPY[word_count] // 8)
    return str(bip39.mnemonic_from_bytes(entropy))


def validate_mnemonic(mnemonic: str) -> dict[str, Any]:
    """Validate a BIP-39 mnemonic's word count, wordlist and checksum.

    Returns a result dict rather than raising, so callers can report *why* a
    phrase is invalid. Use :func:`require_valid_mnemonic` when you just want it
    to fail.
    """
    words = (mnemonic or "").strip().split()
    word_count = len(words)

    if word_count not in VALID_WORD_COUNTS:
        return {
            "valid": False,
            "word_count": word_count,
            "error": (f"Invalid word count: {word_count}. Valid: {list(VALID_WORD_COUNTS)}"),
        }

    try:
        bip39.mnemonic_to_bytes(" ".join(words))
    except Exception as exc:
        # embit surfaces both unknown words and checksum failures here.
        return {"valid": False, "word_count": word_count, "error": str(exc)}

    return {"valid": True, "word_count": word_count}


def require_valid_mnemonic(mnemonic: str) -> str:
    """Validate a mnemonic and return it normalized, or raise.

    Called before every derivation so a bad phrase fails with a clear message,
    rather than relying on an embit implementation detail to catch it.

    Raises:
        ValueError: If the mnemonic is invalid.
    """
    result = validate_mnemonic(mnemonic)
    if not result["valid"]:
        raise ValueError(f"Invalid mnemonic: {result['error']}")
    return " ".join(mnemonic.strip().split())


def mnemonic_to_seed(mnemonic: str, passphrase: str = "") -> bytes:
    """Convert a validated BIP-39 mnemonic to its 64-byte seed.

    Args:
        mnemonic: The mnemonic phrase; validated before use.
        passphrase: Optional BIP-39 passphrase, sometimes called the
            "13th"/"25th word". A different passphrase yields an entirely
            different wallet, and an empty one is the spec default.

    Returns:
        The 64-byte seed.

    Raises:
        ValueError: If the mnemonic is invalid.
    """
    seed: bytes = bip39.mnemonic_to_seed(require_valid_mnemonic(mnemonic), passphrase or "")
    return seed
