"""Bitcoin unit conversion."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

SATS_PER_BTC = 100_000_000
MAX_SATS = 21_000_000 * SATS_PER_BTC

UNITS = {
    "btc": SATS_PER_BTC,
    "mbtc": 100_000,
    "bit": 100,
    "sat": 1,
    "sats": 1,
    "satoshi": 1,
}

_DISPLAY_PLACES = {"btc": 8, "mbtc": 5, "bit": 2, "sat": 0, "sats": 0, "satoshi": 0}


def sats_to_btc(sats: int) -> str:
    """Format satoshis as a BTC string with 8 decimal places."""
    return f"{Decimal(sats) / Decimal(SATS_PER_BTC):.8f}"


def btc_to_sats(btc: str | float | Decimal) -> int:
    """Convert a BTC amount to whole satoshis.

    Routed through ``Decimal`` because float arithmetic on values like 0.1 BTC
    rounds in ways that lose satoshis.
    """
    try:
        return int((Decimal(str(btc)) * SATS_PER_BTC).to_integral_value())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid BTC amount: {btc!r}") from exc


def convert_units(amount: str, from_unit: str, to_unit: str) -> str:
    """Convert between BTC, mBTC, bits and satoshis.

    Raises:
        ValueError: On an unknown unit, an unparseable amount, a negative
            amount, or a value above the 21 million BTC supply cap.
    """
    from_key = from_unit.strip().lower()
    to_key = to_unit.strip().lower()

    for label, key in (("from_unit", from_key), ("to_unit", to_key)):
        if key not in UNITS:
            raise ValueError(f"Unknown {label}: {key!r}. Valid: btc, mbtc, bit, sat")

    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, AttributeError) as exc:
        raise ValueError(f"Invalid amount: {amount!r}") from exc

    if value < 0:
        raise ValueError(f"Amount must not be negative, got {amount}")

    sats = value * UNITS[from_key]
    if sats > MAX_SATS:
        raise ValueError(
            f"Amount exceeds the 21,000,000 BTC supply cap ({sats / SATS_PER_BTC:.8f} BTC)"
        )

    result = sats / Decimal(UNITS[to_key])
    places = _DISPLAY_PLACES[to_key]
    if places == 0:
        if result != result.to_integral_value():
            raise ValueError(
                f"{amount} {from_unit} is {result} {to_unit}; satoshis are "
                "indivisible, so this cannot be represented exactly"
            )
        return str(int(result))
    return f"{result:.{places}f}"
