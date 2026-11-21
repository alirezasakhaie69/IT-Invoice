import unittest
from types import SimpleNamespace

from app.format import format_input, format_number, parse_int
from app.main import FormView, parse_lines
from app.pdf import _ENV
import app.drive as drive
from app.drive import drive_is_pending, mark_drive_pending, root_folder_id
from app.pdf import DEFAULT_ACCENT, DEFAULT_TEMPLATE, normalize_accent, normalize_template, seller_heading, split_item_text


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
        self.assertEqual(normalize_template(""), DEFAULT_TEMPLATE)
        self.assertEqual(normalize_template("formal"), "formal")
        self.assertEqual(split_item_text("Cable\nInstallation"), ("Cable", "Installation"))


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

    def test_service_account_uploads_into_the_personal_folder(self):
        invoice = SimpleNamespace(language="fa", issue_date=None)
        created = []
        original = drive._create_folder
        drive._create_folder = lambda *args: created.append(args) or "child"
        try:
            parent = drive._destination(
                "token",
                {"mode": "service_account", "folder_id": "folder-9", "folder_template": "/{year}"},
                invoice,
            )
        finally:
            drive._create_folder = original
        self.assertEqual(parent, "folder-9")
        self.assertEqual(created, [])

    def test_personal_folder_falls_back_to_the_google_account(self):
        invoice = SimpleNamespace(language="en", issue_date=None)
        saved = {
            "mode": "service_account",
            "folder_id": "folder-9",
            "folder_template": "",
            "refresh_token": "refresh",
            "oauth_client_id": "client",
            "oauth_client_secret": "secret",
        }
        calls = []

        def fake_upload(token, parent, filename, payload):
            calls.append((token, parent))
            if token == "sa-token":
                raise drive.DriveCallError("Service Accounts do not have storage quota.", 403)
            return "file-1"

        originals = (drive.load_drive, drive._bearer, drive._oauth_access_token, drive._upload_file, drive._under_template)
        drive.load_drive = lambda db: dict(saved)
        drive._bearer = lambda db, data: "sa-token"
        drive._oauth_access_token = lambda db, data, language="en": calls.append("oauth") or "user-token"
        drive._upload_file = fake_upload
        drive._under_template = lambda token, data, invoice, root: root
        try:
            file_id = drive.upload_pdf(None, invoice, b"%PDF", "a.pdf")
        finally:
            drive.load_drive, drive._bearer, drive._oauth_access_token, drive._upload_file, drive._under_template = originals
        self.assertEqual(file_id, "file-1")
        self.assertEqual(calls, [("sa-token", "folder-9"), "oauth", ("user-token", "folder-9")])

    def test_personal_folder_asks_for_google_connect_when_missing(self):
        invoice = SimpleNamespace(language="en", issue_date=None)
        saved = {"mode": "service_account", "folder_id": "folder-9", "refresh_token": ""}

        def fake_upload(token, parent, filename, payload):
            raise drive.DriveCallError("Service Accounts do not have storage quota.", 403)

        originals = (drive.load_drive, drive._bearer, drive._upload_file)
        drive.load_drive = lambda db: dict(saved)
        drive._bearer = lambda db, data: "sa-token"
        drive._upload_file = fake_upload
        try:
            with self.assertRaises(RuntimeError) as caught:
                drive.upload_pdf(None, invoice, b"%PDF", "a.pdf")
        finally:
            drive.load_drive, drive._bearer, drive._upload_file = originals
        self.assertIn("Connect Google", str(caught.exception))

    def test_revoked_google_connection_is_cleared(self):
        saved = {
            "refresh_token": "old-token",
            "oauth_client_id": "client",
            "oauth_client_secret": "secret",
            "access_token": "access",
            "access_expires": 0,
        }
        stored = {}
        originals = (drive._post_form, drive.save_drive)
        drive._post_form = lambda url, fields: (_ for _ in ()).throw(RuntimeError("Token has been expired or revoked."))
        drive.save_drive = lambda db, data: stored.update(data)
        try:
            with self.assertRaises(RuntimeError) as caught:
                drive._oauth_access_token(None, dict(saved), "en")
        finally:
            drive._post_form, drive.save_drive = originals
        self.assertIn("Connect Google", str(caught.exception))
        self.assertEqual(stored.get("refresh_token"), "")
        self.assertEqual(stored.get("access_token"), "")

    def test_service_account_credentials_come_from_a_file(self):
        import json
        import os

        import google.oauth2.service_account as service_account

        seen = {}

        class Creds:
            @staticmethod
            def from_service_account_file(path, scopes=None):
                seen["existed"] = os.path.isfile(path)
                with open(path, encoding="utf-8") as handle:
                    seen["email"] = json.load(handle)["client_email"]
                seen["scopes"] = list(scopes or [])
                seen["path"] = path
                return Creds()

        original = service_account.Credentials.from_service_account_file
        service_account.Credentials.from_service_account_file = Creds.from_service_account_file
        try:
            drive._service_account_credentials({
                "client_email": "sa@example.iam.gserviceaccount.com",
                "token_uri": "https://oauth2.googleapis.com/token",
            })
        finally:
            service_account.Credentials.from_service_account_file = original
        self.assertTrue(seen["existed"])
        self.assertEqual(seen["email"], "sa@example.iam.gserviceaccount.com")
        self.assertIn("https://www.googleapis.com/auth/drive", seen["scopes"])
        self.assertFalse(os.path.exists(seen["path"]))


def _pdf_context():
    return {
        "font_css": "",
        "accent": "#ea580c",
        "accent_soft": "#fff7ed",
        "accent_line": "#fdba74",
        "locale": "en",
        "dir": "ltr",
        "seller": {"title": "Shop", "rows": []},
        "buyer": {"title": "Buyer", "rows": []},
        "title": "Invoice",
        "t": lambda key, **_kw: key,
        "number": "1",
        "issue_date": "2026",
        "due_date": "",
        "lines": [{
            "position": "1",
            "description": "Cable\nInstallation",
            "item_name": "Cable",
            "item_note": "Installation",
            "quantity": "1",
            "unit": "pc",
            "unit_price": "1",
            "discount": "0",
            "tax_percent": "0",
            "total": "1",
        }],
        "blank_labels": [],
        "notes": "",
        "currency": "rial",
        "show_discount": False,
        "show_tax": False,
        "subtotal": "1",
        "discount": "0",
        "tax": "0",
        "total": "1",
        "words": "one",
    }


class LineDescriptionTest(unittest.TestCase):
    def test_description_keeps_line_breaks(self):
        form = FormView({
            "description": ["  Cable\r\nInstallation\r\n  "],
            "quantity": ["2"],
            "unit": ["pc"],
            "unit_price": ["1000"],
            "discount": ["0"],
            "tax_percent": ["0"],
        })
        _raw, parsed, invalid = parse_lines(form, "en")
        self.assertFalse(invalid)
        self.assertEqual(parsed[0]["description"], "Cable\nInstallation")

    def test_pdf_templates_keep_description_breaks(self):
        context = _pdf_context()
        for name in ("pdf/formal.html", "pdf/store.html", "pdf/modern.html"):
            html = _ENV.get_template(name).render(**context)
            self.assertIn("white-space: pre-line", html)
            self.assertIn("Cable\nInstallation", html)
            self.assertIn('class="desc"', html)


if __name__ == "__main__":
    unittest.main()
