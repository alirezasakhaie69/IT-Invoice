import unittest

from app.invoices import compute_line, outstanding, payment_status


class PaymentStatusTest(unittest.TestCase):
    def test_status_is_derived_from_payments(self):
        self.assertEqual(payment_status(100, 0), "unpaid")
        self.assertEqual(payment_status(100, 40), "partial")
        self.assertEqual(payment_status(100, 100), "paid")
        self.assertEqual(payment_status(100, 120), "paid")
        self.assertEqual(outstanding(100, 120), -20)
        self.assertEqual(payment_status(0, 0), "paid")
        self.assertEqual(outstanding(0, 0), 0)

    def test_line_tax_rounds_half_up(self):
        self.assertEqual(compute_line(1, 1, 0, 50)["tax"], 1)
        self.assertEqual(compute_line(1, 1, 0, 49)["tax"], 0)
        line = compute_line(2, 1_500_000, 100_000, 10)
        self.assertEqual(line["gross"], 3_000_000)
        self.assertEqual(line["tax"], 290_000)
        self.assertEqual(line["total"], 3_190_000)
        with self.assertRaises(ValueError):
            compute_line(1, 10, 11, 0)
        with self.assertRaises(ValueError):
            compute_line(0, 10, 0, 0)


if __name__ == "__main__":
    unittest.main()
