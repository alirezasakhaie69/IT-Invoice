import unittest
from types import SimpleNamespace

from app.format import format_input, format_number, parse_int
from app.drive import drive_is_pending, mark_drive_pending, root_folder_id
from app.pdf import DEFAULT_ACCENT, normalize_accent, seller_heading


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


class StoreNameTest(unittest.TestCase):
    def test_store_name_can_be_hidden(self):
        issuer = SimpleNamespace(company="فروشگاه", contact_name="الهام", tax_id="", phone="", address="")
        shown, _rows = seller_heading(SimpleNamespace(show_store_name=True), issuer, "fa")
        hidden, _rows = seller_heading(SimpleNamespace(show_store_name=False), issuer, "fa")
        self.assertEqual(shown, "فروشگاه")
        self.assertEqual(hidden, "الهام")
        self.assertEqual(normalize_accent(""), DEFAULT_ACCENT)
        self.assertEqual(DEFAULT_ACCENT, "#ea580c")


class DrivePendingTest(unittest.TestCase):
    def test_pending_expires(self):
        marker = mark_drive_pending()
        started = int(marker.split(":", 1)[1])
        self.assertTrue(drive_is_pending(marker, started + 10))
        self.assertFalse(drive_is_pending(marker, started + 601))
        self.assertFalse(drive_is_pending("HTTP 504"))

    def test_service_account_uses_a_folder_id(self):
        self.assertEqual(root_folder_id({"folder_id": "folder-1", "shared_drive_id": "old"}), "folder-1")
        self.assertEqual(root_folder_id({"shared_drive_id": "old-drive"}), "old-drive")
        self.assertEqual(root_folder_id({}), "")


if __name__ == "__main__":
    unittest.main()
