import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import utcnow
from app.dates import to_jalali
from app.format import western_digits
from app.models import Invoice, InvoiceLine, Payment, Sequence


class NumberTaken(Exception):
    pass


def compute_line(quantity: int, unit_price: int, discount: int, tax_percent: int) -> dict:
    if quantity == 0 or unit_price < 0 or discount < 0 or not 0 <= tax_percent <= 100:
        raise ValueError("line")
    gross = quantity * unit_price
    if discount > abs(gross):
        raise ValueError("discount")
    magnitude = abs(gross) - discount
    base = magnitude if gross >= 0 else -magnitude
    tax = int((Decimal(base) * Decimal(tax_percent) / Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"gross": gross, "discount": discount, "base": base, "tax": tax, "total": base + tax}


def invoice_money(invoice: Invoice) -> dict:
    parts = [compute_line(line.quantity, line.unit_price, line.discount, line.tax_percent) for line in invoice.lines]
    return {
        "gross": sum(part["gross"] for part in parts),
        "discount": sum(part["discount"] for part in parts),
        "tax": sum(part["tax"] for part in parts),
        "total": sum(part["total"] for part in parts),
        "lines": parts,
    }


def paid_amount(invoice: Invoice) -> int:
    return sum(payment.amount for payment in invoice.payments)


def payment_status(total: int, paid: int) -> str:
    if paid <= 0 and total > 0:
        return "unpaid"
    if paid < total:
        return "partial"
    return "paid"


def outstanding(total: int, paid: int) -> int:
    return total - paid


def present(invoice: Invoice) -> dict:
    money = invoice_money(invoice)
    paid = paid_amount(invoice)
    return {
        "invoice": invoice,
        "money": money,
        "paid": paid,
        "outstanding": outstanding(money["total"], paid),
        "pay_status": payment_status(money["total"], paid),
        "line_views": [
            {"line": line, **part}
            for line, part in zip(invoice.lines, money["lines"])
        ],
    }


def sequence_key(_doc_type: str, year: int) -> str:
    """Invoices and pro formas share one counter per Solar Hijri year."""
    return f"doc-{year}"


def peek_number(db, doc_type: str, issue: date) -> str:
    year = to_jalali(issue)[0]
    row = db.get(Sequence, sequence_key(doc_type, year))
    last = row.last_value if row else 0
    return f"{year}-{last + 1:03d}"


def normalize_doc_number(raw: str) -> str:
    text = western_digits(raw or "").strip().replace(" ", "")
    match = re.fullmatch(r"(\d{4})-(\d{1,6})", text)
    if not match:
        raise ValueError("number")
    serial = int(match.group(2))
    if serial < 1:
        raise ValueError("number")
    return f"{int(match.group(1)):04d}-{serial:03d}"


def claim_number(db, doc_type: str, issue: date, raw: str = "", exclude_id: int | None = None) -> str:
    chosen = normalize_doc_number(raw) if (raw or "").strip() else peek_number(db, doc_type, issue)
    year_text, serial_text = chosen.split("-")
    year, serial = int(year_text), int(serial_text)
    stmt = select(Invoice.id).where(Invoice.number == chosen)
    if exclude_id is not None:
        stmt = stmt.where(Invoice.id != exclude_id)
    if db.scalar(stmt):
        raise NumberTaken()
    key = sequence_key(doc_type, year)
    row = db.get(Sequence, key)
    if row is None:
        row = Sequence(name=key, last_value=0)
        db.add(row)
        db.flush()
    if serial > row.last_value:
        row.last_value = serial
        db.flush()
    return chosen


def next_number(db, doc_type: str, issue: date) -> str:
    return claim_number(db, doc_type, issue, "")


def clone_invoice(db, source: Invoice, locale: str, today: date) -> Invoice:
    number = next_number(db, source.doc_type, today)
    now = utcnow()
    clone = Invoice(
        doc_type=source.doc_type,
        number=number,
        proforma_number=number if source.doc_type == "proforma" else None,
        template=source.template,
        accent=getattr(source, "accent", None) or "#ea580c",
        show_store_name=bool(getattr(source, "show_store_name", True)),
        language=locale if locale in ("fa", "en") else "fa",
        customer_id=source.customer_id,
        currency=source.currency,
        issue_date=today,
        due_date=None,
        brief_note="",
        notes=source.notes,
        delivery="not_sent",
        sending=False,
        drive_file_id="",
        drive_error="",
        created_at=now,
        updated_at=now,
    )
    for line in source.lines:
        clone.lines.append(InvoiceLine(
            position=line.position,
            description=line.description,
            quantity=line.quantity,
            unit=line.unit,
            unit_price=line.unit_price,
            discount=line.discount,
            tax_percent=line.tax_percent,
        ))
    db.add(clone)
    db.flush()
    return clone


def convert_proforma(db, invoice: Invoice) -> None:
    if invoice.doc_type != "proforma":
        raise ValueError("not proforma")
    if invoice.proforma_number is None:
        invoice.proforma_number = invoice.number
    invoice.number = next_number(db, "invoice", invoice.issue_date)
    invoice.doc_type = "invoice"
    invoice.delivery = "not_sent"
    invoice.updated_at = utcnow()
    db.flush()


def book_rows(db, start: date, end: date, customer_id: int | None = None) -> tuple[list[dict], dict]:
    stmt = (
        select(Invoice)
        .where(Invoice.doc_type == "invoice", Invoice.issue_date >= start, Invoice.issue_date <= end)
        .options(selectinload(Invoice.lines), selectinload(Invoice.payments), selectinload(Invoice.customer))
        .order_by(Invoice.issue_date, Invoice.number)
    )
    if customer_id:
        stmt = stmt.where(Invoice.customer_id == customer_id)
    invoices = db.scalars(stmt).unique().all()
    rows = []
    buckets: dict[str, dict[str, int]] = {}
    for invoice in invoices:
        view = present(invoice)
        rows.append({
            "number": invoice.number,
            "customer": invoice.customer.name if invoice.customer else "",
            "customer_id": invoice.customer_id,
            "currency": invoice.currency,
            "issue_date": invoice.issue_date,
            "total": view["money"]["total"],
            "paid": view["paid"],
            "outstanding": view["outstanding"],
        })
        bucket = buckets.setdefault(invoice.currency, {"issued": 0, "paid": 0, "outstanding": 0})
        bucket["issued"] += view["money"]["total"]
        bucket["paid"] += view["paid"]
        bucket["outstanding"] += view["outstanding"]
    return rows, buckets


def replace_lines(invoice: Invoice, rows: list[dict]) -> None:
    invoice.lines.clear()
    for index, row in enumerate(rows, start=1):
        invoice.lines.append(InvoiceLine(position=index, **row))
