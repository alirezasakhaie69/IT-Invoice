import unittest
from types import SimpleNamespace

from app.contracts import contract_balance, due_period, statement_rows, term_months, tile_tone
from app.dates import from_jalali


class ContractScheduleTest(unittest.TestCase):
    def test_aban_turns_orange_until_mehr_is_paid(self):
        start = from_jalali(1405, 7, 1)
        aban = from_jalali(1405, 8, 17)
        months = term_months(start, 12)
        self.assertEqual(months[0], (1405, 7))
        self.assertEqual(months[-1], (1406, 6))
        self.assertEqual(due_period(aban), (1405, 7))
        self.assertEqual(tile_tone(months, set(), aban), "due")
        self.assertEqual(tile_tone(months, {(1405, 7)}, aban), "ok")
        self.assertEqual(tile_tone(months, set(), from_jalali(1405, 7, 10)), "ok")

    def test_checked_month_reduces_the_balance(self):
        self.assertEqual(contract_balance(1_000_000, 12, [1_000_000]), (12_000_000, 1_000_000, 11_000_000))
        self.assertEqual(contract_balance(1_000_000, 12, []), (12_000_000, 0, 12_000_000))

    def test_after_the_term_the_last_month_decides_the_color(self):
        months = term_months(from_jalali(1404, 1, 1), 1)
        later = from_jalali(1405, 8, 1)
        self.assertEqual(tile_tone(months, set(), later), "due")
        self.assertEqual(tile_tone(months, {(1404, 1)}, later), "ok")

    def test_farvardin_collects_esfand(self):
        months = term_months(from_jalali(1404, 12, 1), 2)
        today = from_jalali(1405, 1, 5)
        self.assertEqual(due_period(today), (1404, 12))
        self.assertEqual(tile_tone(months, set(), today), "due")
        self.assertEqual(tile_tone(months, {(1404, 12)}, today), "ok")

    def test_statement_names_the_due_month(self):
        start = from_jalali(1405, 7, 1)
        today = from_jalali(1405, 8, 17)
        payment = SimpleNamespace(paid_on=today)
        rows = statement_rows(start, 3, 500, {(1405, 7): payment}, today, "en")
        self.assertEqual(rows[0]["label"], "Mehr 1405")
        self.assertTrue(rows[0]["paid"])
        self.assertTrue(rows[0]["due"])
        self.assertEqual(rows[1]["label"], "Aban 1405")
        self.assertFalse(rows[1]["due"])
