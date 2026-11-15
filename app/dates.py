from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import jdatetime

from app.format import localize_digits

_MONTHS_FA = (
    "فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور",
    "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند",
)
_MONTHS_EN = (
    "Farvardin", "Ordibehesht", "Khordad", "Tir", "Mordad", "Shahrivar",
    "Mehr", "Aban", "Azar", "Dey", "Bahman", "Esfand",
)
_WEEKDAYS_FA = ("ش", "ی", "د", "س", "چ", "پ", "ج")


def to_jalali(value: date) -> tuple[int, int, int]:
    converted = jdatetime.date.fromgregorian(date=value)
    return converted.year, converted.month, converted.day


def from_jalali(year: int, month: int, day: int) -> date:
    return jdatetime.date(year, month, day).togregorian()


def format_date(value: date | None, locale: str) -> str:
    if value is None:
        return ""
    if locale == "fa":
        year, month, day = to_jalali(value)
        return localize_digits(f"{year:04d}/{month:02d}/{day:02d}", "fa")
    return value.isoformat()


def format_iso(value: str | None, locale: str) -> str:
    if not value:
        return ""
    return format_date(date.fromisoformat(value), locale)


def query_flag(value: object) -> int:
    """Turn a query flag into 0 or 1. Booleans render as True/False and must still be accepted."""
    return 1 if str(value).strip().lower() in {"1", "true", "on"} else 0


def jalali_parts(value: str | None, today: date | None = None) -> tuple[int, int]:
    if value:
        try:
            year, month, _day = to_jalali(date.fromisoformat(value))
            return year, month
        except ValueError:
            pass
    year, month, _day = to_jalali(today or date.today())
    return year, month


def days_in_jalali_month(year: int, month: int) -> int:
    if month <= 6:
        return 31
    if month <= 11:
        return 30
    try:
        from_jalali(year, 12, 30)
    except ValueError:
        return 29
    return 30


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def month_cells(year: int, month: int) -> list[date | None]:
    first = from_jalali(year, month, 1)
    offset = (first.weekday() - 5) % 7
    cells: list[date | None] = [None] * offset
    for day in range(1, days_in_jalali_month(year, month) + 1):
        cells.append(from_jalali(year, month, day))
    while len(cells) % 7:
        cells.append(None)
    return cells


def month_label(year: int, month: int, locale: str) -> str:
    return format_period(year, month, locale)


def format_period(year: int, month: int, locale: str) -> str:
    names = _MONTHS_FA if locale == "fa" else _MONTHS_EN
    return f"{names[month - 1]} {localize_digits(str(year), locale)}"


def weekday_labels() -> tuple[str, ...]:
    return _WEEKDAYS_FA


def format_timestamp(value: datetime, locale: str) -> str:
    try:
        local = value.replace(tzinfo=timezone.utc).astimezone(ZoneInfo("Asia/Tehran"))
    except Exception:
        local = value
    clock = localize_digits(f"{local.hour:02d}:{local.minute:02d}", locale)
    return f"{format_date(local.date(), locale)} {clock}"


def default_period(today: date, locale: str) -> tuple[date, date]:
    if locale == "fa":
        year, month, _day = to_jalali(today)
        return from_jalali(year, month, 1), today
    return today.replace(day=1), today
