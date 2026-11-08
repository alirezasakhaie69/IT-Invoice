import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

os.environ.setdefault("APP_SECRET_KEY", "unit-test-secret")
os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base, make_engine
from app.invoices import clone_invoice, convert_proforma, payment_status, present
from app.models import Customer, Invoice, InvoiceLine, Payment, Sequence, User


class CloneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        url = "sqlite:///" + (Path(self.tmp.name) / "t.db").as_posix()
        self.engine = make_engine(url)
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine, expire_on_commit=False)()
        now = datetime(2026, 5, 1, 8, 0, 0)
        self.user = User(username="ada", password_hash="x", role="user", locale="en", created_at=now)
        self.customer = Customer(name="North", phone="", address="", tax_id="", bale_id="", telegram_id="", created_at=now)
        self.db.add_all([
            self.user,
            self.customer,
            Sequence(name="invoice-1398", last_value=0),
            Sequence(name="proforma-1398", last_value=4),
        ])
        self.db.flush()
        self.source = Invoice(
            doc_type="proforma",
            number="1398-004",
            proforma_number="1398-004",
            template="formal",
            language="fa",
            customer_id=self.customer.id,
            currency="toman",
            issue_date=date(2020, 1, 5),
            due_date=date(2020, 2, 5),
            brief_note="internal",
            delivery="sent",
            sending=False,
            drive_file_id="",
            drive_error="",
            created_at=now,
            updated_at=now,
        )
        self.source.lines.append(InvoiceLine(
            position=1, description="Support", quantity=2, unit="hour",
            unit_price=500, discount=100, tax_percent=10,
        ))
        self.db.add(self.source)
        self.db.flush()
        self.db.add(Payment(invoice_id=self.source.id, paid_on=date(2020, 1, 6), amount=200, method="card", note="part"))
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def test_clone_resets_delivery_payment_and_dates(self):
        today = date(2026, 5, 2)
        clone = clone_invoice(self.db, self.source, self.user.locale, today)
        self.db.commit()
        self.assertNotEqual(clone.id, self.source.id)
        self.assertEqual(clone.number, "1405-001")
        self.assertEqual(clone.doc_type, "proforma")
        self.assertEqual(clone.template, "formal")
        self.assertEqual(clone.language, "en")
        self.assertEqual(clone.currency, "toman")
        self.assertEqual(clone.customer_id, self.source.customer_id)
        self.assertEqual(clone.issue_date, today)
        self.assertIsNone(clone.due_date)
        self.assertEqual(clone.brief_note, "")
        self.assertEqual(clone.delivery, "not_sent")
        self.assertEqual(len(clone.lines), 1)
        copied = clone.lines[0]
        self.assertEqual(copied.description, "Support")
        self.assertEqual(copied.quantity, 2)
        self.assertEqual(copied.unit, "hour")
        self.assertEqual(copied.unit_price, 500)
        self.assertEqual(copied.discount, 100)
        self.assertEqual(copied.tax_percent, 10)
        self.assertNotEqual(copied.id, self.source.lines[0].id)
        payments = self.db.scalar(select(func.count()).select_from(Payment).where(Payment.invoice_id == clone.id))
        self.assertEqual(payments, 0)
        view = present(clone)
        self.assertEqual(view["pay_status"], "unpaid")
        self.assertEqual(self.source.number, "1398-004")
        self.assertEqual(self.source.delivery, "sent")
        self.assertEqual(self.source.brief_note, "internal")

    def test_convert_keeps_payments_and_proforma_number(self):
        convert_proforma(self.db, self.source)
        self.db.commit()
        self.assertEqual(self.source.doc_type, "invoice")
        self.assertEqual(self.source.proforma_number, "1398-004")
        self.assertEqual(self.source.number, "1398-001")
        self.assertEqual(self.source.delivery, "not_sent")
        self.assertEqual(len(self.source.payments), 1)
        self.assertEqual(payment_status(present(self.source)["money"]["total"], present(self.source)["paid"]), "partial")


if __name__ == "__main__":
    unittest.main()
