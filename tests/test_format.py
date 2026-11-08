import unittest

from app.format import format_input, format_number, parse_int


class NumberFormatTest(unittest.TestCase):
    def test_thousands_and_digits(self):
        self.assertEqual(format_number(1_000_000, "en"), "1,000,000")
        self.assertEqual(format_number(1_000_000, "fa"), "۱,۰۰۰,۰۰۰")
        self.assertEqual(format_number(-1000, "en"), "-1,000")
        self.assertEqual(format_number(0, "fa"), "۰")
        self.assertEqual(parse_int("۱,۰۰۰,۰۰۰"), 1000000)
        self.assertEqual(parse_int("1,000"), 1000)
        self.assertEqual(format_input("1000000", "fa"), "۱,۰۰۰,۰۰۰")
        self.assertEqual(format_input("۱,۰۰۰", "en"), "1,000")


if __name__ == "__main__":
    unittest.main()
