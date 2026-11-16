from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

MILLIMES_PER_TND = 1000
CURRENCY = "TND"


def to_millimes(amount_tnd: Decimal | str | int | float) -> int:
    try:
        value = Decimal(str(amount_tnd))
    except InvalidOperation as exc:
        raise ValueError(f"Not a valid amount: {amount_tnd!r}") from exc
    millimes = (value * MILLIMES_PER_TND).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(millimes)


def to_tnd(millimes: int) -> Decimal:
    return (Decimal(millimes) / MILLIMES_PER_TND).quantize(Decimal("0.001"))


def fmt(millimes: int, with_currency: bool = True) -> str:
    text = f"{to_tnd(millimes):,.3f}"
    return f"{text} {CURRENCY}" if with_currency else text


def plain(millimes: int) -> str:
    return f"{to_tnd(millimes):.3f}"

