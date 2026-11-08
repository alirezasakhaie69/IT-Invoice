import re

LATIN = "0123456789"
PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_INDIC = "٠١٢٣٤٥٦٧٨٩"
_TO_LATIN = str.maketrans(PERSIAN + ARABIC_INDIC, LATIN + LATIN)
_TO_PERSIAN = str.maketrans(LATIN, PERSIAN)

_ONES_FA = ["", "یک", "دو", "سه", "چهار", "پنج", "شش", "هفت", "هشت", "نه"]
_TEENS_FA = ["ده", "یازده", "دوازده", "سیزده", "چهارده", "پانزده", "شانزده", "هفده", "هجده", "نوزده"]
_TENS_FA = ["", "", "بیست", "سی", "چهل", "پنجاه", "شصت", "هفتاد", "هشتاد", "نود"]
_HUNDREDS_FA = ["", "صد", "دویست", "سیصد", "چهارصد", "پانصد", "ششصد", "هفتصد", "هشتصد", "نهصد"]
_SCALES_FA = ((10**12, "تریلیون"), (10**9, "میلیارد"), (10**6, "میلیون"), (10**3, "هزار"))

_ONES_EN = [
    "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
_TENS_EN = ["", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"]
_SCALES_EN = ((10**12, "trillion"), (10**9, "billion"), (10**6, "million"), (10**3, "thousand"))


def western_digits(text: str) -> str:
    return (text or "").translate(_TO_LATIN)


def localize_digits(text: str, locale: str) -> str:
    if locale == "fa":
        return text.translate(_TO_PERSIAN)
    return text.translate(_TO_LATIN)


def format_number(value: int, locale: str) -> str:
    """Group thousands and apply the locale's digits. This is the only number formatter."""
    number = int(value)
    text = f"{abs(number):,}"
    text = localize_digits(text, locale)
    if number < 0:
        return f"-{text}"
    return text


def parse_int(value: str) -> int:
    text = (value or "").strip().translate(_TO_LATIN)
    text = text.replace(",", "").replace("٬", "").replace(" ", "")
    if not re.fullmatch(r"-?\d+", text):
        raise ValueError("number")
    return int(text)


def format_input(value: str, locale: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    try:
        return format_number(parse_int(text), locale)
    except ValueError:
        return localize_digits(text, locale)


def _under_1000_fa(number: int) -> str:
    parts: list[str] = []
    if number >= 100:
        parts.append(_HUNDREDS_FA[number // 100])
        number %= 100
    if number >= 20:
        parts.append(_TENS_FA[number // 10])
        if number % 10:
            parts.append(_ONES_FA[number % 10])
    elif number >= 10:
        parts.append(_TEENS_FA[number - 10])
    elif number:
        parts.append(_ONES_FA[number])
    return " و ".join(parts)


def _words_fa(number: int) -> str:
    if number == 0:
        return "صفر"
    parts: list[str] = []
    for scale, name in _SCALES_FA:
        if number >= scale:
            parts.append(f"{_under_1000_fa(number // scale)} {name}")
            number %= scale
    if number:
        parts.append(_under_1000_fa(number))
    return " و ".join(parts)


def _under_1000_en(number: int) -> str:
    parts: list[str] = []
    if number >= 100:
        parts.append(f"{_ONES_EN[number // 100]} hundred")
        number %= 100
    if number >= 20:
        word = _TENS_EN[number // 10]
        if number % 10:
            word += "-" + _ONES_EN[number % 10]
        parts.append(word)
    elif number:
        parts.append(_ONES_EN[number])
    return " ".join(parts)


def _words_en(number: int) -> str:
    if number == 0:
        return "zero"
    parts: list[str] = []
    for scale, name in _SCALES_EN:
        if number >= scale:
            parts.append(f"{_under_1000_en(number // scale)} {name}")
            number %= scale
    if number:
        parts.append(_under_1000_en(number))
    return " ".join(parts)


def amount_in_words(amount: int, currency: str, locale: str) -> str:
    value = int(amount)
    if locale == "fa":
        unit = "ریال" if currency == "rial" else "تومان"
        words = f"{_words_fa(abs(value))} {unit}"
        return f"منفی {words}" if value < 0 else words
    unit = "rial" if currency == "rial" else "toman"
    words = f"{_words_en(abs(value))} {unit}"
    return f"minus {words}" if value < 0 else words
