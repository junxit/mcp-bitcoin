"""Input validation shared by tools and providers.

embit's address parsing is deliberately network-agnostic: for base58 it tries
every network in turn, and for bech32 it derives the HRP from the address itself
and never compares it to an expected network. That means a mainnet address will
happily parse on a testnet-configured server and return the wrong chain's balance.
Every address entering this server goes through :func:`validate_address` first.
"""

from __future__ import annotations

import re

from embit.script import Script, address_to_scriptpubkey

from mcp_bitcoin.config import Network

TXID_RE = re.compile(r"^[0-9a-fA-F]{64}$")

# bech32/bech32m HRPs, longest first so "bcrt" wins over "bc"
_BECH32_HRPS: tuple[tuple[str, tuple[Network, ...]], ...] = (
    ("bcrt1", (Network.REGTEST,)),
    ("tb1", (Network.TESTNET, Network.SIGNET)),
    ("bc1", (Network.MAINNET,)),
)

# base58 version-byte first characters. Testnet, signet and regtest are
# genuinely indistinguishable here — they share version bytes by design.
_BASE58_PREFIXES: tuple[tuple[tuple[str, ...], tuple[Network, ...]], ...] = (
    (("1", "3"), (Network.MAINNET,)),
    (("m", "n", "2"), (Network.TESTNET, Network.SIGNET, Network.REGTEST)),
)


def networks_for_address(address: str) -> tuple[Network, ...]:
    """Return the networks an address could belong to, by prefix alone.

    Returns an empty tuple if the prefix matches no known network. Does not
    verify the checksum — use :func:`validate_address` for that.
    """
    lowered = address.lower()
    for hrp, networks in _BECH32_HRPS:
        if lowered.startswith(hrp):
            return networks
    for prefixes, networks in _BASE58_PREFIXES:
        if address.startswith(prefixes):
            return networks
    return ()


def script_type_of(script: Script) -> str:
    """Identify a scriptPubKey type from its raw bytes."""
    data = script.data
    if len(data) == 25 and data[0] == 0x76 and data[-2:] == b"\x88\xac":
        return "P2PKH"
    if len(data) == 23 and data[0] == 0xA9 and data[-1] == 0x87:
        return "P2SH"
    if len(data) == 22 and data[0] == 0x00 and data[1] == 0x14:
        return "P2WPKH"
    if len(data) == 34 and data[0] == 0x00 and data[1] == 0x20:
        return "P2WSH"
    if len(data) == 34 and data[0] == 0x51 and data[1] == 0x20:
        return "P2TR"
    return "unknown"


def validate_address(address: str, network: Network) -> Script:
    """Validate an address's checksum *and* that it belongs to ``network``.

    Args:
        address: The Bitcoin address to check.
        network: The network the address must belong to.

    Returns:
        The decoded scriptPubKey.

    Raises:
        ValueError: If the address is malformed or is for a different network.
    """
    if not address or not address.strip():
        raise ValueError("Address is empty")
    address = address.strip()

    candidates = networks_for_address(address)
    if not candidates:
        raise ValueError(
            f"Unrecognized address prefix: {address[:6]!r}. "
            f"Expected one of {', '.join(network.address_prefixes)} for {network.value}."
        )

    if network not in candidates:
        belongs_to = " or ".join(sorted(n.value for n in candidates))
        raise ValueError(
            f"Address {address} is a {belongs_to} address, but this server is "
            f"configured for {network.value}. Querying it would return data from "
            f"the wrong chain."
        )

    try:
        return address_to_scriptpubkey(address)
    except Exception as exc:
        raise ValueError(f"Invalid address {address}: {exc}") from exc


def validate_txid(txid: str) -> str:
    """Validate a transaction ID and return it lowercased."""
    if not TXID_RE.match(txid or ""):
        raise ValueError(f"Invalid txid: expected 64 hex characters, got {txid!r}")
    return txid.lower()


def validate_hex(value: str, field: str = "value") -> bytes:
    """Validate and decode a hex string."""
    try:
        return bytes.fromhex(value.strip())
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"Invalid hex in {field}: {exc}") from exc
