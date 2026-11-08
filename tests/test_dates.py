import unittest
from datetime import date

from app.dates import format_date, from_jalali, to_jalali


class CalendarConversionTest(unittest.TestCase):
    def test_jalali_and_gregorian_round_trip(self):
        new_year = date(2024, 3, 20)
        self.assertEqual(to_jalali(new_year), (1403, 1, 1))
        self.assertEqual(from_jalali(1403, 1, 1), new_year)
        self.assertEqual(format_date(new_year, "en"), "2024-03-20")
        shown = format_date(new_year, "fa")
        self.assertEqual(shown, "۱۴۰۳/۰۱/۰۱")
        for value in (date(2026, 11, 8), date(2020, 1, 1), date(2030, 12, 31)):
            year, month, day = to_jalali(value)
            self.assertEqual(from_jalali(year, month, day), value)


if __name__ == "__main__":
    unittest.main()
