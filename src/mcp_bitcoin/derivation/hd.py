"""BIP-32 HD key derivation."""

from __future__ import annotations

import re

from embit import bip32

from mcp_bitcoin.config import Network

# Which SLIP-132 version bytes each standard serializes its extended keys with.
# embit's NETWORKS table already carries per-network values for all of these.
STANDARD_VERSION_KEYS = {
    "bip44": ("xpub", "xprv"),
    "bip49": ("ypub", "yprv"),
    "bip84": ("zpub", "zprv"),
    "bip86": ("xpub", "xprv"),  # Taproot uses plain xpub
}

# A derivation path: "m", optionally followed by /index with an optional
# hardened marker. Rejects negative numbers, empty components and stray text.
PATH_PATTERN = re.compile(r"^m(/\d+['h]?)*$")

HARDENED_OFFSET = 0x80000000


def parse_derivation_path(path: str) -> list[int]:
    """Parse a BIP-32 derivation path into child indices.

    Args:
        path: A path such as ``m/84'/0'/0'/0/0``. Both ``'`` and ``h`` are
            accepted as hardened markers.

    Returns:
        Child indices, with ``0x80000000`` added for hardened components.

    Raises:
        ValueError: If the path is malformed or any index is out of range.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("Derivation path is empty")
    path = path.strip()

    if not PATH_PATTERN.match(path):
        raise ValueError(
            f"Invalid derivation path: {path!r}. Expected a path like \"m/84'/0'/0'/0/0\"."
        )

    if path == "m":
        return []

    indices = []
    for component in path.split("/")[1:]:
        hardened = component[-1] in ("'", "h")
        index = int(component.rstrip("'h"))
        if index >= HARDENED_OFFSET:
            raise ValueError(
                f"Child index out of range in {path!r}: {index} (must be < {HARDENED_OFFSET})"
            )
        indices.append(index + HARDENED_OFFSET if hardened else index)
    return indices


def master_key_from_seed(seed: bytes) -> bip32.HDKey:
    """Derive a BIP-32 master key from a seed."""
    return bip32.HDKey.from_seed(seed)


def derive_key(parent: bip32.HDKey, path: str) -> bip32.HDKey:
    """Derive a child key from a parent using a derivation path.

    The path is validated before use, so malformed input fails with a clear
    message rather than whatever embit happens to raise.

    Args:
        parent: The parent (usually master) HD key.
        path: BIP-32 derivation path.

    Returns:
        The derived HD key.

    Raises:
        ValueError: If the path is malformed.
    """
    indices = parse_derivation_path(path)
    return parent.derive(indices) if indices else parent


def xpub_from_key(key: bip32.HDKey, standard: str, network: Network) -> str:
    """Serialize an HD key as an extended public key.

    Args:
        key: HD key, private or public.
        standard: One of ``bip44``, ``bip49``, ``bip84``, ``bip86``.
        network: Target network, which selects the version bytes.

    Returns:
        A base58 extended public key (xpub/ypub/zpub, or tpub/upub/vpub).

    Raises:
        ValueError: If ``standard`` is unknown.
    """
    if standard not in STANDARD_VERSION_KEYS:
        raise ValueError(f"Unknown standard: {standard}. Valid: {', '.join(STANDARD_VERSION_KEYS)}")
    pub_key, _ = STANDARD_VERSION_KEYS[standard]
    version = network.embit_network[pub_key]
    return str(key.to_public().to_base58(version=version))


def key_from_xpub(xpub: str) -> bip32.HDKey:
    """Parse an extended public key (xpub/ypub/zpub/tpub/upub/vpub).

    Raises:
        ValueError: If the key is malformed or fails its checksum.
    """
    try:
        return bip32.HDKey.from_base58(xpub.strip())
    except Exception as exc:
        raise ValueError(f"Invalid extended public key: {exc}") from exc
