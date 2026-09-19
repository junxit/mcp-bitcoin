"""BIP-44/49/84/86 standard derivation paths and address generation."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from embit import bip32, ec

from mcp_bitcoin.config import Network
from mcp_bitcoin.derivation.addresses import (
    pubkey_to_address,
    pubkey_to_p2pkh,
    pubkey_to_p2sh_p2wpkh,
    pubkey_to_p2tr,
    pubkey_to_p2wpkh,
)
from mcp_bitcoin.derivation.hd import derive_key
from mcp_bitcoin.types import DerivedAddress


class StandardSpec(NamedTuple):
    """How a BIP standard maps to a purpose number and an address encoding."""

    purpose: int
    address_type: str
    encode: Callable[[ec.PublicKey, Network], str]


STANDARDS: dict[str, StandardSpec] = {
    "bip44": StandardSpec(44, "legacy", pubkey_to_p2pkh),
    "bip49": StandardSpec(49, "nested_segwit", pubkey_to_p2sh_p2wpkh),
    "bip84": StandardSpec(84, "native_segwit", pubkey_to_p2wpkh),
    "bip86": StandardSpec(86, "taproot", pubkey_to_p2tr),
}

ADDRESS_TYPE_TO_STANDARD = {
    "legacy": "bip44",
    "p2pkh": "bip44",
    "nested_segwit": "bip49",
    "p2sh-p2wpkh": "bip49",
    "native_segwit": "bip84",
    "p2wpkh": "bip84",
    "bech32": "bip84",
    "taproot": "bip86",
    "p2tr": "bip86",
    "bech32m": "bip86",
}

MAX_ADDRESS_COUNT = 500


def standard_for_address_type(address_type: str) -> str:
    """Map an address type to the BIP standard that derives it."""
    standard = ADDRESS_TYPE_TO_STANDARD.get(address_type.lower())
    if standard is None:
        raise ValueError(
            f"Unknown address type: {address_type}. "
            "Valid: legacy, nested_segwit, native_segwit, taproot"
        )
    return standard


def build_account_path(standard: str, network: Network, account: int = 0) -> str:
    """Build the account-level path for a standard, e.g. ``m/84'/0'/0'``."""
    if standard not in STANDARDS:
        raise ValueError(f"Unknown standard: {standard}. Valid: {', '.join(STANDARDS)}")
    if account < 0:
        raise ValueError(f"Account index must be non-negative, got {account}")
    return f"m/{STANDARDS[standard].purpose}'/{network.coin_type}'/{account}'"


def _check_range(change: int, start_index: int, count: int) -> None:
    if change not in (0, 1):
        raise ValueError(f"change must be 0 (receive) or 1 (change), got {change}")
    if start_index < 0:
        raise ValueError(f"start_index must be non-negative, got {start_index}")
    if not 1 <= count <= MAX_ADDRESS_COUNT:
        raise ValueError(f"count must be between 1 and {MAX_ADDRESS_COUNT}, got {count}")


def derive_addresses(
    root_key: bip32.HDKey,
    standard: str,
    network: Network,
    account: int = 0,
    change: int = 0,
    start_index: int = 0,
    count: int = 10,
) -> list[DerivedAddress]:
    """Derive a range of addresses from a master key using a BIP standard.

    Args:
        root_key: Master HD key.
        standard: ``bip44``, ``bip49``, ``bip84`` or ``bip86``.
        network: Target network.
        account: Account index.
        change: 0 for receiving addresses, 1 for change.
        start_index: First address index.
        count: How many addresses to derive.

    Returns:
        Derived addresses with their full absolute derivation paths.
    """
    _check_range(change, start_index, count)
    spec = STANDARDS[standard]
    account_path = build_account_path(standard, network, account)
    account_key = derive_key(root_key, account_path)

    addresses = []
    for i in range(start_index, start_index + count):
        child = account_key.derive([change, i])
        pubkey = child.to_public().key if child.is_private else child.key
        addresses.append(
            DerivedAddress(
                path=f"{account_path}/{change}/{i}",
                address=spec.encode(pubkey, network),
                public_key=pubkey.sec().hex(),
                address_type=spec.address_type,
            )
        )
    return addresses


def derive_addresses_from_xpub(
    xpub_key: bip32.HDKey,
    address_type: str,
    network: Network,
    change: int = 0,
    start_index: int = 0,
    count: int = 10,
    account_path: str | None = None,
) -> list[DerivedAddress]:
    """Derive addresses from an account-level extended public key (watch-only).

    Args:
        xpub_key: Account-level xpub/ypub/zpub, i.e. at ``m/purpose'/coin'/account'``.
        address_type: Address encoding to produce.
        network: Target network.
        change: 0 for receiving addresses, 1 for change.
        start_index: First address index.
        count: How many addresses to derive.
        account_path: If known, the account path this xpub sits at, so the
            reported paths are absolute and match the mnemonic-derived ones.

    Returns:
        Derived addresses.
    """
    _check_range(change, start_index, count)
    prefix = f"{account_path}/" if account_path else ""

    addresses = []
    for i in range(start_index, start_index + count):
        child = xpub_key.derive([change, i])
        pubkey = child.to_public().key if child.is_private else child.key
        addresses.append(
            DerivedAddress(
                path=f"{prefix}{change}/{i}",
                address=pubkey_to_address(pubkey, address_type, network),
                public_key=pubkey.sec().hex(),
                address_type=address_type,
            )
        )
    return addresses
