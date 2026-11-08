from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import utcnow
from app.models import Invoice, InvoiceLine, Payment, Sequence


def compute_line(quantity: int, unit_price: int, discount: int, tax_percent: int) -> dict:
    if quantity < 1 or unit_price < 0 or discount < 0 or not 0 <= tax_percent <= 100:
        raise ValueError("line")
    gross = quantity * unit_price
    if discount > gross:
        raise ValueError("discount")
    base = gross - discount
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


def next_number(db, doc_type: str) -> int:
    row = db.get(Sequence, doc_type)
    if row is None:
        row = Sequence(name=doc_type, last_value=0)
        db.add(row)
        db.flush()
    row.last_value += 1
    db.flush()
    return row.last_value


def clone_invoice(db, source: Invoice, locale: str, today: date) -> Invoice:
    number = next_number(db, source.doc_type)
    now = utcnow()
    clone = Invoice(
        doc_type=source.doc_type,
        number=number,
        proforma_number=number if source.doc_type == "proforma" else None,
        template=source.template,
        language=locale if locale in ("fa", "en") else "fa",
        customer_id=source.customer_id,
        currency=source.currency,
        issue_date=today,
        due_date=None,
        brief_note="",
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
    invoice.number = next_number(db, "invoice")
    invoice.doc_type = "invoice"
    invoice.delivery = "not_sent"
    invoice.updated_at = utcnow()
    db.flush()


def book_rows(db, start: date, end: date) -> tuple[list[dict], dict]:
    invoices = db.scalars(
        select(Invoice)
        .where(Invoice.doc_type == "invoice", Invoice.issue_date >= start, Invoice.issue_date <= end)
        .options(selectinload(Invoice.lines), selectinload(Invoice.payments), selectinload(Invoice.customer))
        .order_by(Invoice.issue_date, Invoice.number)
    ).unique().all()
    rows = []
    buckets: dict[str, dict[str, int]] = {}
    for invoice in invoices:
        view = present(invoice)
        rows.append({
            "number": invoice.number,
            "customer": invoice.customer.name if invoice.customer else "",
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
