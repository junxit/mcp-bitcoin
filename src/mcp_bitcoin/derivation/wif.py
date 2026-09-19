"""WIF (Wallet Import Format) decoding."""

from __future__ import annotations

from typing import Any

from embit import ec

from mcp_bitcoin.config import Network
from mcp_bitcoin.derivation.addresses import (
    pubkey_to_p2pkh,
    pubkey_to_p2sh_p2wpkh,
    pubkey_to_p2tr,
    pubkey_to_p2wpkh,
)

# WIF version-byte first characters. Test networks share version byte 0xef.
_MAINNET_PREFIXES = ("5", "K", "L")
_TESTNET_PREFIXES = ("9", "c")


def networks_for_wif(wif: str) -> tuple[Network, ...]:
    """Return the networks a WIF key could belong to, by prefix."""
    if not wif:
        return ()
    if wif[0] in _MAINNET_PREFIXES:
        return (Network.MAINNET,)
    if wif[0] in _TESTNET_PREFIXES:
        return (Network.TESTNET, Network.SIGNET, Network.REGTEST)
    return ()


def decode_wif(wif: str, network: Network) -> dict[str, Any]:
    """Decode a WIF private key into its public components.

    Args:
        wif: The WIF-encoded private key.
        network: The network the key must belong to.

    Returns:
        Public key, compression flag, and the four address encodings. The
        private key itself is deliberately **not** returned — see the note below.

    Raises:
        ValueError: If the key is malformed or is for a different network.

    Note:
        Earlier revisions returned ``private_key_hex``. They no longer do:
        a tool result lands in the model's context and the client's saved
        transcript, so echoing the secret back doubles its exposure for no
        benefit the caller doesn't already have.
    """
    wif = (wif or "").strip()
    if not wif:
        raise ValueError("WIF key is empty")

    candidates = networks_for_wif(wif)
    if not candidates:
        raise ValueError(
            f"Unrecognized WIF prefix {wif[0]!r}. Expected one of "
            f"{', '.join(_MAINNET_PREFIXES + _TESTNET_PREFIXES)}."
        )
    if network not in candidates:
        belongs_to = " or ".join(sorted(n.value for n in candidates))
        raise ValueError(
            f"This WIF key is for {belongs_to}, but the server is configured for {network.value}."
        )

    try:
        secret = ec.PrivateKey.from_wif(wif)
    except Exception as exc:
        raise ValueError(f"Invalid WIF key: {exc}") from exc

    pubkey = secret.get_public_key()
    compressed = len(pubkey.sec()) == 33

    result: dict[str, Any] = {
        "public_key": pubkey.sec().hex(),
        "compressed": compressed,
        "network": network.value,
        "addresses": {"legacy": pubkey_to_p2pkh(pubkey, network)},
    }

    # SegWit and Taproot are only defined for compressed keys.
    if compressed:
        result["addresses"].update(
            {
                "nested_segwit": pubkey_to_p2sh_p2wpkh(pubkey, network),
                "native_segwit": pubkey_to_p2wpkh(pubkey, network),
                "taproot": pubkey_to_p2tr(pubkey, network),
            }
        )
    else:
        result["note"] = (
            "Uncompressed key: only legacy P2PKH is valid. SegWit and Taproot "
            "require a compressed public key."
        )

    return result
