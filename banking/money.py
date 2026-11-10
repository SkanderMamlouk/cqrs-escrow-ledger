"""
Money helpers.

Golden rule of banking software: NEVER store money as a float
(0.1 + 0.2 = 0.30000000000000004 !).  We store every amount as an INTEGER
number of millimes.  The Tunisian dinar (TND) has 1000 millimes, so

    1 TND        = 1000 millimes
    1500.250 TND = 1500250 millimes
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MILLIMES_PER_TND = 1000
CURRENCY = "TND"


def to_millimes(amount_tnd: Decimal | str | int | float) -> int:
    """'1500.25' TND -> 1500250 millimes (exact, no float rounding)."""
    try:
        value = Decimal(str(amount_tnd))
    except InvalidOperation as exc:
        raise ValueError(f"Not a valid amount: {amount_tnd!r}") from exc
    millimes = (value * MILLIMES_PER_TND).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(millimes)


def to_tnd(millimes: int) -> Decimal:
    """1500250 millimes -> Decimal('1500.250')."""
    return (Decimal(millimes) / MILLIMES_PER_TND).quantize(Decimal("0.001"))


def fmt(millimes: int, with_currency: bool = True) -> str:
    """1500250 -> '1,500.250 TND' (human readable)."""
    text = f"{to_tnd(millimes):,.3f}"
    return f"{text} {CURRENCY}" if with_currency else text


def plain(millimes: int) -> str:
    """1500250 -> '1500.250' (machine readable, used by the AI verifier)."""
    return f"{to_tnd(millimes):.3f}"
