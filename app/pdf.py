import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.dates import format_date
from app.format import amount_in_words, format_number, localize_digits
from app.i18n import translate
from app.invoices import invoice_money

FONT_DIR = Path(__file__).resolve().parent / "static" / "fonts"
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
_ENV = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]))

_WEIGHTS = (
    ("Regular", 400),
    ("Medium", 500),
    ("Bold", 700),
    ("ExtraBold", 800),
    ("Black", 900),
)


def font_css() -> str:
    rules = []
    for name, weight in _WEIGHTS:
        uri = (FONT_DIR / f"Vazirmatn-{name}.ttf").resolve().as_uri()
        rules.append(
            "@font-face {"
            f" font-family: Vazirmatn;"
            f" src: url('{uri}') format('truetype');"
            f" font-weight: {weight};"
            " font-style: normal;"
            "}"
        )
    return "\n".join(rules)


def _party(title: str, rows: list[tuple[str, str]]) -> dict:
    return {"title": title, "rows": rows}


def render_invoice_pdf(invoice, customer, issuer) -> bytes:
    locale = invoice.language if invoice.language in ("fa", "en") else "fa"
    money = invoice_money(invoice)
    lines = []
    for line, part in zip(invoice.lines, money["lines"]):
        lines.append({
            "position": format_number(line.position, locale),
            "description": line.description,
            "quantity": format_number(line.quantity, locale),
            "unit": line.unit,
            "unit_price": format_number(line.unit_price, locale),
            "discount": format_number(line.discount, locale),
            "tax_percent": format_number(line.tax_percent, locale),
            "total": format_number(part["total"], locale),
        })
    seller_rows = []
    seller_title = issuer.company or issuer.contact_name or translate(locale, "empty_seller")
    if issuer.company and issuer.contact_name:
        seller_rows.append((translate(locale, "contact"), issuer.contact_name))
    seller_rows.extend([
        (translate(locale, "tax_id"), issuer.tax_id),
        (translate(locale, "phone"), issuer.phone),
        (translate(locale, "address"), issuer.address),
    ])
    buyer_rows = [
        (translate(locale, "tax_id"), customer.tax_id),
        (translate(locale, "phone"), customer.phone),
        (translate(locale, "address"), customer.address),
    ]
    title_key = "title_invoice" if invoice.doc_type == "invoice" else "title_proforma"
    context = {
        "locale": locale,
        "dir": "rtl" if locale == "fa" else "ltr",
        "font_css": font_css(),
        "t": lambda key, **kwargs: translate(locale, key, **kwargs),
        "title": translate(locale, title_key),
        "number": localize_digits(str(invoice.number), locale),
        "issue_date": format_date(invoice.issue_date, locale),
        "due_date": format_date(invoice.due_date, locale) or translate(locale, "dash"),
        "currency": translate(locale, invoice.currency),
        "lines": lines,
        "blank_labels": [
            format_number(len(lines) + 1 + index, locale)
            for index in range(max(0, 6 - len(lines)))
        ] if invoice.template == "store" else [],
        "seller": _party(seller_title, seller_rows),
        "buyer": _party(customer.name, buyer_rows),
        "show_discount": money["discount"] > 0,
        "show_tax": money["tax"] > 0,
        "subtotal": format_number(money["gross"], locale),
        "discount": format_number(money["discount"], locale),
        "tax": format_number(money["tax"], locale),
        "total": format_number(money["total"], locale),
        "words": amount_in_words(money["total"], invoice.currency, locale),
        "notes": (getattr(invoice, "notes", "") or "").strip(),
        **accent_style(getattr(invoice, "accent", "")),
    }
    name = "pdf/store.html" if invoice.template == "store" else "pdf/formal.html"
    html = _ENV.get_template(name).render(**context)
    from weasyprint import HTML
    return HTML(string=html, base_url=str(FONT_DIR)).write_pdf()


FORMAL_COLORS = (
    ("red", "#dc2626"),
    ("blue", "#2563eb"),
    ("orange", "#ea580c"),
    ("green", "#16a34a"),
    ("teal", "#0d9488"),
)
_FORMAL_HEX = {color.lower(): color for _name, color in FORMAL_COLORS}


def normalize_accent(value: str) -> str:
    return _FORMAL_HEX.get((value or "").strip().lower(), "#2563eb")


def accent_style(value: str) -> dict:
    color = normalize_accent(value)
    red, green, blue = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    return {
        "accent": color,
        "accent_soft": f"rgba({red}, {green}, {blue}, 0.06)",
        "accent_line": f"rgba({red}, {green}, {blue}, 0.2)",
    }


def pdf_filename(invoice, customer_name: str) -> str:
    number = str(invoice.number)
    name = re.sub(r'[\\/:*?"<>|]+', " ", customer_name or "").strip() or "—"
    if getattr(invoice, "language", "fa") == "fa":
        kind = "پیش‌فاکتور" if invoice.doc_type == "proforma" else "فاکتور"
        return f"{kind} شماره {number} - مشتری {name}.pdf"
    kind = "Pro forma" if invoice.doc_type == "proforma" else "Invoice"
    return f"{kind} {number} - {name}.pdf"
