import unittest
from types import SimpleNamespace

from app.format import format_input, format_number, parse_int
import app.drive as drive
from app.drive import drive_is_pending, explain_drive_error, mark_drive_pending, root_folder_id
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

    def test_folder_lookup_stays_inside_the_parent(self):
        seen = []

        def fake(method, url, token, body=None, content_type=None):
            seen.append(url)
            return {"files": [{"id": "year-folder", "name": "1405"}]}

        original = drive._api
        drive._api = fake
        try:
            found = drive._find_folder("token", "1405", "parent-id")
        finally:
            drive._api = original
        self.assertEqual(found, "year-folder")
        self.assertEqual(len(seen), 1)
        self.assertIn("parent-id", seen[0])
        self.assertNotIn("corpora=allDrives", seen[0])
        self.assertIn("includeItemsFromAllDrives=true", seen[0])

    def test_personal_folder_upload_skips_shared_drive_flag_until_needed(self):
        seen = []

        def fake(method, url, token, body=None, content_type=None):
            seen.append(url)
            if "supportsAllDrives" not in url:
                raise drive.DriveCallError("File not found", 404)
            return {"id": "file-1"}

        original = drive._api
        drive._api = fake
        try:
            created = drive._api_my_drive("POST", "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", "token", b"x", "application/pdf")
        finally:
            drive._api = original
        self.assertEqual(created["id"], "file-1")
        self.assertEqual(len(seen), 2)
        self.assertNotIn("supportsAllDrives", seen[0])
        self.assertIn("supportsAllDrives=true", seen[1])

    def test_service_account_quota_is_explained(self):
        text = explain_drive_error("Service Accounts do not have storage quota.")
        self.assertIn("OAuth", text)
        self.assertNotIn("storage quota", text.lower())


if __name__ == "__main__":
    unittest.main()
