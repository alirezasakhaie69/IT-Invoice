"""Support-contract schedules. A month's fee is due the following month."""

from datetime import date

from app.dates import format_period, shift_month, to_jalali


def term_months(start: date, duration: int) -> list[tuple[int, int]]:
    year, month, _day = to_jalali(start)
    return [shift_month(year, month, index) for index in range(duration)]


def due_period(today: date) -> tuple[int, int]:
    year, month, _day = to_jalali(today)
    return shift_month(year, month, -1)


def _ordinal(period: tuple[int, int]) -> int:
    return period[0] * 12 + period[1]


def focus_period(months: list[tuple[int, int]], today: date) -> tuple[int, int] | None:
    """The month whose fee decides the tile: last month, or the final month once the term is over."""
    if not months:
        return None
    due = due_period(today)
    if _ordinal(due) < _ordinal(months[0]):
        return None
    if _ordinal(due) <= _ordinal(months[-1]):
        return due
    return months[-1]


def tile_tone(months: list[tuple[int, int]], paid: set[tuple[int, int]], today: date) -> str:
    """Green when the fee that fell due last month is paid. Orange when it is not."""
    target = focus_period(months, today)
    if target is None:
        return "ok"
    return "ok" if target in paid else "due"


def contract_balance(monthly_amount: int, duration: int, paid_amounts: list[int]) -> tuple[int, int, int]:
    total = monthly_amount * duration
    paid = sum(paid_amounts)
    return total, paid, total - paid


def statement_rows(start: date, duration: int, monthly_amount: int, payments: dict, today: date, locale: str) -> list[dict]:
    focus = focus_period(term_months(start, duration), today)
    rows = []
    for year, month in term_months(start, duration):
        payment = payments.get((year, month))
        rows.append({
            "key": f"{year}-{month:02d}",
            "year": year,
            "month": month,
            "label": format_period(year, month, locale),
            "amount": monthly_amount,
            "paid": payment is not None,
            "paid_on": payment.paid_on.isoformat() if payment is not None else "",
            "due": (year, month) == focus,
        })
    return rows
