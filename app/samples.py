from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.pdf import render_invoice_pdf


def _issuer():
    return SimpleNamespace(
        company="شرکت نمونه پارس",
        contact_name="الهام کریمی",
        phone="02100000000",
        address="تهران، خیابان مثال",
        tax_id="10101010101",
    )


def _customer(name, address):
    return SimpleNamespace(name=name, phone="09120000000", address=address, tax_id="0088008800")


def _invoice(**kwargs):
    lines = kwargs.pop("lines")
    data = dict(
        doc_type="invoice",
        number="1405-012",
        accent="#ea580c",
        proforma_number=None,
        template="store",
        language="fa",
        currency="rial",
        issue_date=date(2026, 3, 21),
        due_date=date(2026, 4, 4),
        notes="گارانتی ۱۸ ماهه شرکتی — تحویل کالا در محل خریدار",
    )
    data.update(kwargs)
    data["lines"] = lines
    return SimpleNamespace(**data)


def _lines(unit):
    return [
        SimpleNamespace(position=1, description="خدمات مشاوره و پشتیبانی سامانه", quantity=2, unit=unit, unit_price=1_500_000, discount=100_000, tax_percent=10),
        SimpleNamespace(position=2, description="Technical support" if unit == "hour" else "بازدید دوره‌ای", quantity=1, unit=unit, unit_price=250_000, discount=0, tax_percent=0),
    ]


def main():
    out = Path("samples")
    out.mkdir(parents=True, exist_ok=True)
    persian = render_invoice_pdf(
        _invoice(template="store", language="fa", currency="rial", lines=_lines("عدد")),
        _customer("رضا محمدی", "اصفهان، خیابان نمونه"),
        _issuer(),
    )
    english_lines = [
        SimpleNamespace(position=1, description="Consulting and support", quantity=2, unit="hour", unit_price=1_500_000, discount=100_000, tax_percent=10),
        SimpleNamespace(position=2, description="Site visit", quantity=1, unit="hour", unit_price=250_000, discount=0, tax_percent=0),
    ]
    english = render_invoice_pdf(
        _invoice(
            template="formal", language="en", currency="toman", lines=english_lines,
            notes="18-month company warranty. Delivery at the buyer's address.",
        ),
        _customer("Reza Mohammadi", "Isfahan, Sample Street"),
        SimpleNamespace(company="Sample Pars Co.", contact_name="Elham Karimi", phone="02100000000", address="Tehran, Example Street", tax_id="10101010101"),
    )
    # English issuer should be Latin for the English sheet.
    (out / "invoice-fa-store.pdf").write_bytes(persian)
    (out / "invoice-en-formal.pdf").write_bytes(english)
    print(f"fa bytes={len(persian)} vazirmatn={'Vazirmatn' in persian.decode('latin1', errors='ignore')}")
    print(f"en bytes={len(english)} vazirmatn={'Vazirmatn' in english.decode('latin1', errors='ignore')}")


if __name__ == "__main__":
    main()
