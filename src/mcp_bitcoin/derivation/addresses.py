"""Address encoding from public keys."""

from __future__ import annotations

from embit import ec, script

from mcp_bitcoin.config import Network


def pubkey_to_p2pkh(pubkey: ec.PublicKey, network: Network) -> str:
    """Encode a public key as a P2PKH (Legacy) address."""
    return str(script.p2pkh(pubkey).address(network=network.embit_network))


def pubkey_to_p2sh_p2wpkh(pubkey: ec.PublicKey, network: Network) -> str:
    """Encode a public key as a P2SH-P2WPKH (Nested SegWit) address."""
    return str(script.p2sh(script.p2wpkh(pubkey)).address(network=network.embit_network))


def pubkey_to_p2wpkh(pubkey: ec.PublicKey, network: Network) -> str:
    """Encode a public key as a P2WPKH (Native SegWit / Bech32) address."""
    return str(script.p2wpkh(pubkey).address(network=network.embit_network))


def pubkey_to_p2tr(pubkey: ec.PublicKey, network: Network) -> str:
    """Encode a public key as a P2TR (Taproot / Bech32m) address."""
    return str(script.p2tr(pubkey).address(network=network.embit_network))


ADDRESS_TYPE_MAP = {
    "legacy": pubkey_to_p2pkh,
    "p2pkh": pubkey_to_p2pkh,
    "nested_segwit": pubkey_to_p2sh_p2wpkh,
    "p2sh-p2wpkh": pubkey_to_p2sh_p2wpkh,
    "native_segwit": pubkey_to_p2wpkh,
    "p2wpkh": pubkey_to_p2wpkh,
    "bech32": pubkey_to_p2wpkh,
    "taproot": pubkey_to_p2tr,
    "p2tr": pubkey_to_p2tr,
    "bech32m": pubkey_to_p2tr,
}

VALID_ADDRESS_TYPES = ("legacy", "nested_segwit", "native_segwit", "taproot")


def pubkey_to_address(pubkey: ec.PublicKey, address_type: str, network: Network) -> str:
    """Encode a public key as an address of the specified type.

    Args:
        pubkey: Public key.
        address_type: One of legacy, nested_segwit, native_segwit, taproot
            (SegWit aliases such as ``p2wpkh`` are also accepted).
        network: Target network.

    Returns:
        The encoded address.

    Raises:
        ValueError: If ``address_type`` is not recognized.
    """
    func = ADDRESS_TYPE_MAP.get(address_type.lower())
    if func is None:
        raise ValueError(
            f"Unknown address type: {address_type}. Valid: {', '.join(VALID_ADDRESS_TYPES)}"
        )
    return func(pubkey, network)
