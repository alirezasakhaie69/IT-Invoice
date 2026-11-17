import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("APP_SECRET_KEY", "unit-test-secret")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.models import Customer, Invoice, InvoiceLine, SendLog
from app.send import _limit_segment_size, send_invoice


class SendLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        url = "sqlite:///" + (Path(self.tmp.name) / "t.db").as_posix()
        self.engine = make_engine(url)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        now = datetime(2026, 11, 8, 8, 0, 0)
        self.customer = Customer(
            name="رضا", phone="09120000000", address="", tax_id="",
            bale_id="524794649", telegram_id="", created_at=now,
        )
        self.db.add(self.customer)
        self.db.flush()
        self.invoice = Invoice(
            doc_type="invoice",
            number="1405-001",
            proforma_number=None,
            template="store",
            language="fa",
            customer_id=self.customer.id,
            currency="rial",
            issue_date=date(2026, 11, 8),
            due_date=None,
            brief_note="",
            delivery="not_sent",
            sending=False,
            drive_file_id="",
            drive_error="",
            created_at=now,
            updated_at=now,
        )
        self.invoice.lines.append(InvoiceLine(
            position=1, description="Service", quantity=1, unit="عدد",
            unit_price=1000, discount=0, tax_percent=0,
        ))
        self.db.add(self.invoice)
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def logs(self):
        return self.db.scalars(select(SendLog).where(SendLog.invoice_id == self.invoice.id)).all()

    def test_pdf_failure_is_recorded(self):
        with patch("app.send.write_pdf", side_effect=OSError("library missing")):
            code = send_invoice(self.db, self.invoice, self.customer, None)
        self.assertEqual(code, "send_failed")
        rows = self.logs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].channel, "bale")
        self.assertEqual(rows[0].result, "error")
        self.assertIn("library missing", rows[0].error)
        self.assertFalse(self.invoice.sending)

    def test_yearly_number_can_be_sent(self):
        path = Path(self.tmp.name) / "doc.pdf"
        path.write_bytes(b"%PDF-1.4")

        def fake_write(invoice, customer, issuer):
            return path

        with patch("app.send.write_pdf", side_effect=fake_write), patch("app.send.deliver", return_value=(True, "")) as deliver:
            code = send_invoice(self.db, self.invoice, self.customer, None)
        self.assertEqual(code, "send_ok")
        self.assertEqual(self.invoice.delivery, "sent")
        caption = deliver.call_args.args[4]
        self.assertIn("۱۴۰۵-۰۰۱", caption)
        rows = self.logs()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].result, "ok")
        self.assertEqual(rows[0].error, "")


class TunnelSegmentTest(unittest.TestCase):
    def test_google_packets_stay_small_enough_for_wireguard(self):
        seen = {}

        class Sock:
            def setsockopt(self, level, opt, value):
                seen["value"] = value

        _limit_segment_size(Sock())
        self.assertEqual(seen["value"], 1100)

    def test_unsupported_mss_option_is_ignored(self):
        class Sock:
            def setsockopt(self, level, opt, value):
                raise OSError(10042, "unsupported")

        _limit_segment_size(Sock())
