import csv
import io
import json
import logging
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import date
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.auth import (
    SESSION_TTL,
    admin_count,
    authenticate,
    create_session,
    delete_session,
    get_session,
    hash_password,
    login_csrf_ok,
    make_login_csrf,
    seed_admin,
    tokens_match,
    utcnow,
)
from app.config import app_secret, cookie_secure, data_dir
from app.crypto import redact
from app.contracts import contract_balance, focus_period, statement_rows, term_months, tile_tone
from app.dates import (
    default_period,
    format_date,
    format_iso,
    format_period,
    format_timestamp,
    jalali_parts,
    month_cells,
    month_label,
    query_flag,
    shift_month,
    to_jalali,
    weekday_labels,
)
from app.db import SessionLocal
from app.drive import (
    apply_form,
    authorization_url,
    drive_is_pending,
    exchange_code,
    load_drive,
    mark_drive_pending,
    parse_state,
    public_view,
    save_drive,
    test_connection,
    upload_pdf,
)
from app.format import format_input, format_number, localize_digits, parse_int
from app.i18n import translate
from app.invoices import NumberTaken, book_rows, claim_number, clone_invoice, compute_line, convert_proforma, invoice_money, peek_number, present, replace_lines
from app.models import (
    Contract,
    ContractPayment,
    ContractTerm,
    Customer,
    Invoice,
    Issuer,
    Payment,
    SessionRow,
    User,
)
from app.pdf import DEFAULT_ACCENT, DEFAULT_TEMPLATE, FORMAL_COLORS, normalize_accent, normalize_template, pdf_filename, normalize_transparency
from app.send import channel_token, pdf_path, send_invoice, send_text, write_pdf

log = logging.getLogger("invoice")
logging.basicConfig(level=logging.INFO)

TEMPLATES = Environment(
    loader=FileSystemLoader("app/templates"),
    autoescape=select_autoescape(["html"]),
)
def ensure_archived_column() -> None:
    from sqlalchemy import inspect, text
    from app.db import get_engine

    engine = get_engine()
    names = {column["name"] for column in inspect(engine).get_columns("invoices")}
    if "archived" not in names:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE invoices ADD COLUMN archived BOOLEAN NOT NULL DEFAULT 0"))

FLASH = {
    "saved", "deleted", "send_ok", "send_failed", "no_channel", "send_in_progress",
    "drive_ok", "drive_failed", "drive_started", "test_ok", "test_failed", "connected_msg", "converted",
    "customer_in_use", "cannot_delete_self", "cannot_delete_last_admin", "username_taken",
    "date_invalid", "contract_exists", "term_invalid", "term_active", "term_locked", "invoice_archived", "invoice_restored",
}
ERRORS = {
    "send_failed", "no_channel", "send_in_progress", "drive_failed", "test_failed",
    "customer_in_use", "cannot_delete_self", "cannot_delete_last_admin", "username_taken",
    "date_invalid", "contract_exists", "term_invalid", "term_active", "term_locked",
}


class FormView:
    def __init__(self, pairs: dict):
        self.pairs = pairs

    def get(self, key, default=""):
        values = self.pairs.get(key)
        if not values:
            return default
        return values[0]

    def getlist(self, key):
        return list(self.pairs.get(key, []))


@contextmanager
def session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def read_form(request: Request) -> FormView:
    return request.state.form_view


def render(request: Request, name: str, status_code: int = 200, **extra):
    user = getattr(request.state, "user", None)
    locale = extra.pop("locale", None) or (user.locale if user else "fa")
    if locale not in ("fa", "en"):
        locale = "fa"
    code = extra.pop("message", request.query_params.get("m", ""))
    if code not in FLASH:
        code = ""
    context = {
        "request": request,
        "user": user,
        "locale": locale,
        "csrf": extra.pop("csrf", getattr(request.state, "csrf", "")),
        "t": lambda key, **kwargs: translate(locale, key, **kwargs),
        "format_number": lambda value: format_number(int(value), locale),
        "format_code": lambda value: localize_digits(str(value), locale),
        "format_date": lambda value: format_date(value, locale),
        "format_iso": lambda value: format_iso(value, locale),
        "format_timestamp": lambda value: format_timestamp(value, locale),
        "jalali_parts": jalali_parts,
        "message": code,
        "message_error": code in ERRORS,
    }
    context.update(extra)
    return TEMPLATES.get_template(name).render(**context), status_code


def html(request: Request, name: str, status_code: int = 200, **extra) -> HTMLResponse:
    body, status = render(request, name, status_code, **extra)
    return HTMLResponse(body, status_code=status)


def redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=303)


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return ""


def safe_next(request: Request) -> str:
    parsed = urlparse(request.headers.get("referer", ""))
    if parsed.path.startswith("/") and not parsed.path.startswith("//"):
        return parsed.path
    return "/invoices"


def optional_int(value: str):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def blank_line(locale: str) -> dict:
    return {
        "description": "",
        "quantity": "",
        "unit": translate(locale, "unit_default"),
        "unit_price": "",
        "discount": "",
        "tax_percent": "",
    }


def parse_lines(form: FormView, locale: str):
    fields = ("description", "quantity", "unit", "unit_price", "discount", "tax_percent")
    columns = {name: form.getlist(name) for name in fields}
    count = max((len(values) for values in columns.values()), default=0)
    raw = []
    parsed = []
    invalid = False
    default_unit = translate(locale, "unit_default")
    for index in range(count):
        item = {name: (columns[name][index] if index < len(columns[name]) else "") for name in fields}
        raw.append(item)
        if not any(item[name].strip() for name in ("description", "quantity", "unit_price", "discount", "tax_percent")):
            continue
        try:
            quantity = parse_int(item["quantity"])
            price = parse_int(item["unit_price"])
            discount = parse_int(item["discount"]) if item["discount"].strip() else 0
            tax = parse_int(item["tax_percent"]) if item["tax_percent"].strip() else 0
            compute_line(quantity, price, discount, tax)
        except ValueError:
            invalid = True
            continue
        description = item["description"].replace("\r\n", "\n").replace("\r", "\n").strip()
        if not description:
            invalid = True
            continue
        parsed.append({
            "description": description[:2000],
            "quantity": quantity,
            "unit": (item["unit"].strip() or default_unit)[:40],
            "unit_price": price,
            "discount": discount,
            "tax_percent": tax,
        })
    return raw, parsed, invalid


def lines_from_invoice(invoice: Invoice, locale: str) -> list[dict]:
    if not invoice.lines:
        return [blank_line(locale)]
    return [{
        "description": line.description,
        "quantity": format_number(line.quantity, locale),
        "unit": line.unit,
        "unit_price": format_number(line.unit_price, locale),
        "discount": format_number(line.discount, locale),
        "tax_percent": format_number(line.tax_percent, locale),
    } for line in invoice.lines]


def load_invoice(db, invoice_id: int):
    return db.scalar(
        select(Invoice)
        .where(Invoice.id == invoice_id)
        .options(
            selectinload(Invoice.lines),
            selectinload(Invoice.payments),
            selectinload(Invoice.send_logs),
            selectinload(Invoice.customer),
        )
    )


def get_issuer(db) -> Issuer:
    row = db.get(Issuer, 1)
    if row is None:
        row = Issuer(id=1, company="", contact_name="", phone="", address="", tax_id="")
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def require_admin(request: Request):
    if request.state.user.role != "admin":
        return html(request, "forbidden.html", status_code=403)
    return None


def customer_values(source) -> dict:
    return {
        "name": getattr(source, "name", "") or "",
        "phone": getattr(source, "phone", "") or "",
        "address": getattr(source, "address", "") or "",
        "tax_id": getattr(source, "tax_id", "") or "",
        "bale_id": getattr(source, "bale_id", "") or "",
        "telegram_id": getattr(source, "telegram_id", "") or "",
    }


def money_preview(parsed, currency):
    fake = SimpleNamespace(lines=[SimpleNamespace(**row) for row in parsed])
    return invoice_money(fake), currency if currency in ("rial", "toman") else "rial"


@asynccontextmanager
async def lifespan(_app):
    if not app_secret():
        raise SystemExit("APP_SECRET_KEY must be set.")
    data_dir()
    ensure_archived_column()
    with session() as db:
        seed_admin(db)
        db.execute(update(Invoice).values(sending=False))
        db.commit()
        get_issuer(db)
    yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static") or path == "/favicon.ico":
        return await call_next(request)
    db = SessionLocal()
    try:
        row = get_session(db, request.cookies.get("session_id", ""), utcnow())
        user = db.get(User, row.user_id) if row else None
        request.state.user = user
        request.state.csrf = row.csrf_token if row else ""
        request.state.form_view = FormView({})
        if request.method == "POST":
            pairs = parse_qs((await request.body()).decode("utf-8", errors="replace"), keep_blank_values=True)
            request.state.form_view = FormView(pairs)
            presented = request.headers.get("x-csrf-token") or pairs.get("csrf_token", [""])[0]
            if path == "/login":
                if not login_csrf_ok(request.cookies.get("login_csrf", ""), presented):
                    return HTMLResponse(translate("fa", "csrf_failed"), status_code=403)
            else:
                if user is None:
                    return redirect("/login")
                if not tokens_match(row.csrf_token, presented):
                    return HTMLResponse(translate(user.locale, "csrf_failed"), status_code=403)
        elif user is None and path != "/login":
            return redirect("/login")
        if user is not None and path == "/login" and request.method == "GET":
            return redirect("/invoices")
        return await call_next(request)
    finally:
        db.close()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse("app/static/favicon.svg", media_type="image/svg+xml")


@app.get("/")
def root():
    return redirect("/invoices")


@app.get("/login")
def login_form(request: Request):
    token = make_login_csrf()
    response = html(request, "login.html", csrf=token, error="", username="", locale="fa")
    response.set_cookie("login_csrf", token, httponly=True, samesite="lax", secure=cookie_secure(), max_age=3600, path="/")
    return response


@app.post("/login")
def login_submit(request: Request):
    form = read_form(request)
    username = form.get("username").strip()
    with session() as db:
        user, error = authenticate(db, username, form.get("password"), client_ip(request), utcnow())
        if error:
            token = make_login_csrf()
            response = html(request, "login.html", csrf=token, error=error, username=username, locale="fa")
            response.set_cookie("login_csrf", token, httponly=True, samesite="lax", secure=cookie_secure(), max_age=3600, path="/")
            return response
        row = create_session(db, user, utcnow())
        session_id = row.id
    response = redirect("/invoices")
    response.set_cookie(
        "session_id",
        session_id,
        httponly=True,
        samesite="lax",
        secure=cookie_secure(),
        max_age=int(SESSION_TTL.total_seconds()),
        path="/",
    )
    return response


@app.post("/logout")
def logout(request: Request):
    with session() as db:
        delete_session(db, request.cookies.get("session_id", ""))
    response = redirect("/login")
    response.delete_cookie("session_id", path="/")
    return response


@app.post("/locale")
def set_locale(request: Request):
    choice = read_form(request).get("locale")
    if choice in ("fa", "en"):
        with session() as db:
            user = db.get(User, request.state.user.id)
            user.locale = choice
            db.commit()
    return redirect(safe_next(request))


@app.post("/format/number")
def format_number_field(request: Request):
    return PlainTextResponse(format_input(read_form(request).get("value"), request.state.user.locale))


@app.get("/calendar")
def calendar(request: Request, year: int = 0, month: int = 0, selected: str = "", clear: str = "0"):
    if not (1 <= month <= 12 and 1200 <= year <= 1600):
        year, month = jalali_parts("")
    previous = shift_month(year, month, -1)
    nxt = shift_month(year, month, 1)
    show_clear = query_flag(clear)
    locale = request.state.user.locale
    cells = []
    for cell in month_cells(year, month):
        if cell is None:
            cells.append(None)
            continue
        cells.append({
            "iso": cell.isoformat(),
            "label": format_date(cell, locale),
            "day": to_jalali(cell)[2],
        })
    today = date.today()
    return html(
        request,
        "partials/calendar.html",
        year=year,
        month=month,
        prev_year=previous[0],
        prev_month=previous[1],
        next_year=nxt[0],
        next_month=nxt[1],
        cells=cells,
        selected=selected,
        clear=show_clear,
        label=month_label(year, month, locale),
        weekdays=weekday_labels(),
        today_iso=today.isoformat(),
        today_label=format_date(today, locale),
    )


@app.get("/invoices/line-row")
def line_row(request: Request):
    return html(request, "partials/line_row.html", line=blank_line(request.state.user.locale))


@app.post("/invoices/preview")
def preview(request: Request):
    form = read_form(request)
    _raw, parsed, invalid = parse_lines(form, request.state.user.locale)
    money, currency = money_preview(parsed, form.get("currency") or "rial")
    return html(request, "partials/totals.html", money=money, currency=currency, invalid=invalid)


@app.get("/invoices")
def invoice_list(
    request: Request,
    customer: int = 0,
    q: str = "",
    doc_type: str = "",
    delivery: str = "",
    pay: str = "",
    sort: str = "date_desc",
):
    q = q.strip()
    if doc_type not in ("", "invoice", "proforma"):
        doc_type = ""
    if delivery not in ("", "sent", "not_sent"):
        delivery = ""
    if pay not in ("", "paid", "partial", "unpaid"):
        pay = ""
    if sort not in ("date_asc", "date_desc", "number_asc", "number_desc"):
        sort = "date_desc"
    with session() as db:
        stmt = (
            select(Invoice)
            .options(selectinload(Invoice.lines), selectinload(Invoice.payments), selectinload(Invoice.customer))
        )
        if customer:
            stmt = stmt.where(Invoice.customer_id == customer)
        if doc_type:
            stmt = stmt.where(Invoice.doc_type == doc_type)
        if delivery:
            stmt = stmt.where(Invoice.delivery == delivery)
        show_archived = request.query_params.get("archived") == "1"
        stmt = stmt.where(Invoice.archived.is_(show_archived))
        invoices = db.scalars(stmt).unique().all()
        rows = [present(invoice) for invoice in invoices]
        if pay:
            rows = [row for row in rows if row["pay_status"] == pay]
        if q:
            needle = q.casefold()

            def matches(row) -> bool:
                invoice = row["invoice"]
                customer_name = invoice.customer.name if invoice.customer else ""
                parts = (
                    invoice.number,
                    invoice.proforma_number or "",
                    customer_name,
                    invoice.brief_note,
                    invoice.notes,
                )
                return any(needle in (part or "").casefold() for part in parts)

            rows = [row for row in rows if matches(row)]
        reverse = sort.endswith("_desc")
        if sort.startswith("date"):
            rows.sort(key=lambda row: (row["invoice"].issue_date, row["invoice"].id), reverse=reverse)
        else:
            rows.sort(key=lambda row: (row["invoice"].number.casefold(), row["invoice"].id), reverse=reverse)
        person = db.get(Customer, customer) if customer else None
        buckets = {}
        if person is not None:
            _summary_rows, buckets = book_rows(db, date.min, date.max, person.id)
        return html(
            request,
            "invoices.html",
            rows=rows,
            show_archived=show_archived,
            buckets=buckets,
            customer_name=person.name if person else "",
            customer_id=customer,
            filters={
                "q": q,
                "doc_type": doc_type,
                "delivery": delivery,
                "pay": pay,
                "sort": sort,
            },
        )

def form_page(request, customers, invoice, values, lines, errors):
    return html(
        request,
        "invoice_form.html",
        customers=customers,
        invoice=invoice,
        errors=errors,
        lines=lines,
        selected_customer=values["customer_id"],
        doc_type=values["doc_type"],
        template_name=values["template"],
        language=values["language"],
        currency=values["currency"],
        issue_date=values["issue_date"],
        due_date=values["due_date"],
        brief_note=values["brief_note"],
        notes=values["notes"],
        money=values["money"],
        invalid=values["invalid"],
        number=values["number"],
        accent=normalize_accent(values["accent"]),
        show_store_name=values["show_store_name"],
        formal_colors=FORMAL_COLORS,
    )


def empty_invoice_values(locale: str) -> dict:
    return {
        "customer_id": None,
        "doc_type": "invoice",
        "template": DEFAULT_TEMPLATE,
        "language": locale,
        "currency": "rial",
        "issue_date": date.today().isoformat(),
        "due_date": "",
        "brief_note": "",
        "notes": "",
        "money": invoice_money(SimpleNamespace(lines=[])),
        "invalid": False,
        "number": "",
        "accent": DEFAULT_ACCENT,
        "show_store_name": True,
    }


@app.get("/invoices/new")
def new_invoice(request: Request):
    locale = request.state.user.locale
    with session() as db:
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        values = empty_invoice_values(locale)
        values["number"] = peek_number(db, "invoice", date.today())
        return form_page(request, customers, None, values, [blank_line(locale)], [])


@app.post("/invoices")
def create_invoice(request: Request):
    return save_invoice(request, None)


@app.get("/invoices/{invoice_id}")
def invoice_detail(request: Request, invoice_id: int, transparency: int = 25):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        view = present(invoice)
        today = date.today().isoformat()
        level = normalize_transparency(transparency) if invoice.template == "modern" else 0
        return html(
            request,
            "invoice_detail.html",
            view=view,
            today=today,
            drive_pending=drive_is_pending(invoice.drive_error),
            transparency=level,
        )


@app.get("/invoices/{invoice_id}/edit")
def edit_invoice(request: Request, invoice_id: int):
    locale = request.state.user.locale
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        values = {
            "customer_id": invoice.customer_id,
            "doc_type": invoice.doc_type,
            "template": invoice.template,
            "language": invoice.language,
            "currency": invoice.currency,
            "issue_date": invoice.issue_date.isoformat(),
            "due_date": invoice.due_date.isoformat() if invoice.due_date else "",
            "brief_note": invoice.brief_note,
            "notes": invoice.notes,
            "money": invoice_money(invoice),
            "invalid": False,
            "number": invoice.number,
            "accent": normalize_accent(invoice.accent),
            "show_store_name": invoice.show_store_name,
        }
        lines = lines_from_invoice(invoice, locale)
        return form_page(request, customers, invoice, values, lines, [])


@app.post("/invoices/{invoice_id}")
def update_invoice(request: Request, invoice_id: int):
    return save_invoice(request, invoice_id)


def save_invoice(request: Request, invoice_id: int | None):
    form = read_form(request)
    locale = request.state.user.locale
    raw, parsed, invalid = parse_lines(form, locale)
    errors = []
    if invalid:
        errors.append(translate(locale, "line_invalid"))
    customer_id = optional_int(form.get("customer_id"))
    if not customer_id:
        errors.append(translate(locale, "customer_required"))
    try:
        issue = date.fromisoformat(form.get("issue_date"))
    except ValueError:
        issue = None
        errors.append(translate(locale, "date_required"))
    due = None
    if form.get("due_date"):
        try:
            due = date.fromisoformat(form.get("due_date"))
        except ValueError:
            errors.append(translate(locale, "date_invalid"))
    with session() as db:
        invoice = load_invoice(db, invoice_id) if invoice_id else None
        if invoice_id and invoice is None:
            return html(request, "missing.html", status_code=404)
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        doc_type = invoice.doc_type if invoice else form.get("doc_type")
        if doc_type not in ("invoice", "proforma"):
            doc_type = "invoice"
        template_name = normalize_template(form.get("template"))
        language = form.get("language") if form.get("language") in ("fa", "en") else locale
        currency = invoice.currency if invoice else form.get("currency")
        if currency not in ("rial", "toman"):
            currency = "rial"
        accent = normalize_accent(form.get("accent"))
        show_store_name = form.get("show_store_name") == "on"
        if customer_id and db.get(Customer, customer_id) is None:
            errors.append(translate(locale, "customer_required"))
        values = {
            "customer_id": customer_id,
            "doc_type": doc_type,
            "template": template_name,
            "language": language,
            "currency": currency,
            "issue_date": form.get("issue_date"),
            "due_date": form.get("due_date"),
            "brief_note": form.get("brief_note")[:280],
            "notes": form.get("notes").strip()[:400],
            "money": money_preview(parsed, currency)[0],
            "invalid": invalid,
            "number": form.get("number"),
            "accent": accent,
            "show_store_name": show_store_name,
        }
        if errors or issue is None:
            return form_page(request, customers, invoice, values, raw or [blank_line(locale)], errors)
        try:
            number = claim_number(db, doc_type, issue, form.get("number"), invoice.id if invoice else None)
        except NumberTaken:
            errors.append(translate(locale, "number_taken"))
            return form_page(request, customers, invoice, values, raw or [blank_line(locale)], errors)
        except ValueError:
            errors.append(translate(locale, "number_invalid"))
            return form_page(request, customers, invoice, values, raw or [blank_line(locale)], errors)
        now = utcnow()
        if invoice is None:
            invoice = Invoice(
                doc_type=doc_type,
                number=number,
                proforma_number=number if doc_type == "proforma" else None,
                template=template_name,
                accent=accent,
                show_store_name=show_store_name,
                language=language,
                customer_id=customer_id,
                currency=currency,
                issue_date=issue,
                due_date=due,
                brief_note=values["brief_note"],
                notes=values["notes"],
                delivery="not_sent",
                sending=False,
                drive_file_id="",
                drive_error="",
                created_at=now,
                updated_at=now,
            )
            db.add(invoice)
            db.flush()
        else:
            previous_number = invoice.number
            invoice.number = number
            if invoice.doc_type == "proforma" and invoice.proforma_number == previous_number:
                invoice.proforma_number = number
            invoice.template = template_name
            invoice.accent = accent
            invoice.show_store_name = show_store_name
            invoice.language = language
            invoice.customer_id = customer_id
            invoice.issue_date = issue
            invoice.due_date = due
            invoice.brief_note = values["brief_note"]
            invoice.notes = values["notes"]
            invoice.updated_at = now
        replace_lines(invoice, parsed)
        db.commit()
        target = invoice.id
    return redirect(f"/invoices/{target}?m=saved")


@app.post("/invoices/{invoice_id}/delete")
def delete_invoice(request: Request, invoice_id: int):
    with session() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        db.delete(invoice)
        db.commit()
    path = pdf_path(invoice_id)
    if path.is_file():
        path.unlink()
    return redirect("/invoices?m=deleted")

@app.post("/invoices/{invoice_id}/archive")
def archive_invoice(request: Request, invoice_id: int):
    with session() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        invoice.archived = True
        db.commit()
    return redirect("/invoices?m=invoice_archived")


@app.post("/invoices/{invoice_id}/unarchive")
def unarchive_invoice(request: Request, invoice_id: int):
    with session() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        invoice.archived = False
        db.commit()
    return redirect("/invoices?archived=1&m=invoice_restored")


@app.post("/invoices/{invoice_id}/clone")
def clone_route(request: Request, invoice_id: int):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        clone = clone_invoice(db, invoice, request.state.user.locale, date.today())
        db.commit()
        target = clone.id
    return redirect(f"/invoices/{target}/edit")


@app.post("/invoices/{invoice_id}/convert")
def convert_route(request: Request, invoice_id: int):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        if invoice.doc_type != "proforma":
            return redirect(f"/invoices/{invoice_id}")
        convert_proforma(db, invoice)
        db.commit()
    return redirect(f"/invoices/{invoice_id}?m=converted")


@app.post("/invoices/{invoice_id}/delivery")
def delivery_route(request: Request, invoice_id: int):
    value = read_form(request).get("delivery")
    with session() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        if value in ("sent", "not_sent"):
            invoice.delivery = value
            invoice.updated_at = utcnow()
            db.commit()
    return redirect(f"/invoices/{invoice_id}?m=saved")


@app.post("/invoices/{invoice_id}/payments")
def add_payment(request: Request, invoice_id: int):
    form = read_form(request)
    locale = request.state.user.locale
    with session() as db:
        invoice = db.get(Invoice, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        try:
            paid_on = date.fromisoformat(form.get("paid_on"))
            amount = parse_int(form.get("amount"))
            if amount <= 0:
                raise ValueError("amount")
        except ValueError:
            return redirect(f"/invoices/{invoice_id}?m=date_invalid")
        db.add(Payment(
            invoice_id=invoice.id,
            paid_on=paid_on,
            amount=amount,
            method=form.get("method").strip()[:80],
            note=form.get("note").strip()[:280],
        ))
        invoice.updated_at = utcnow()
        db.commit()
    return redirect(f"/invoices/{invoice_id}?m=saved")


@app.post("/invoices/{invoice_id}/payments/{payment_id}/delete")
def delete_payment(request: Request, invoice_id: int, payment_id: int):
    with session() as db:
        payment = db.get(Payment, payment_id)
        invoice = db.get(Invoice, invoice_id)
        if payment is None or invoice is None or payment.invoice_id != invoice.id:
            return html(request, "missing.html", status_code=404)
        db.delete(payment)
        invoice.updated_at = utcnow()
        db.commit()
    return redirect(f"/invoices/{invoice_id}?m=deleted")


@app.get("/invoices/{invoice_id}/pdf")
def download_pdf(request: Request, invoice_id: int, transparency: int = 25):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        level = normalize_transparency(transparency) if invoice.template == "modern" else 0
        path = write_pdf(invoice, invoice.customer, get_issuer(db), level)
        filename = pdf_filename(invoice, invoice.customer.name if invoice.customer else "")
    return FileResponse(path, media_type="application/pdf", filename=filename)


@app.get("/invoices/{invoice_id}/preview")
def preview_pdf(request: Request, invoice_id: int, transparency: int = 25):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        level = normalize_transparency(transparency) if invoice.template == "modern" else 0
        path = write_pdf(invoice, invoice.customer, get_issuer(db), level)
        filename = pdf_filename(invoice, invoice.customer.name if invoice.customer else "")
    return FileResponse(path, media_type="application/pdf", filename=filename, content_disposition_type="inline")


@app.post("/invoices/{invoice_id}/send")
def send_route(request: Request, invoice_id: int):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        try:
            code = send_invoice(db, invoice, invoice.customer, get_issuer(db))
        except Exception as exc:
            log.info("send crashed: %s", type(exc).__name__)
            code = "send_failed"
    return redirect(f"/invoices/{invoice_id}?m={code}")


def _save_drive(invoice_id: int) -> None:
    try:
        with session() as db:
            invoice = load_invoice(db, invoice_id)
            if invoice is None:
                return
            path = write_pdf(invoice, invoice.customer, get_issuer(db))
            invoice.drive_file_id = upload_pdf(
                db,
                invoice,
                path.read_bytes(),
                pdf_filename(invoice, invoice.customer.name if invoice.customer else ""),
            )
            invoice.drive_error = ""
            invoice.updated_at = utcnow()
            db.commit()
    except Exception as exc:
        log.info("drive save failed: %s", type(exc).__name__)
        try:
            with session() as db:
                invoice = db.get(Invoice, invoice_id)
                if invoice is not None:
                    invoice.drive_error = redact(str(exc)) or type(exc).__name__
                    invoice.updated_at = utcnow()
                    db.commit()
        except Exception:
            log.info("drive error was not stored")


@app.post("/invoices/{invoice_id}/drive")
def drive_route(request: Request, invoice_id: int):
    with session() as db:
        invoice = load_invoice(db, invoice_id)
        if invoice is None:
            return html(request, "missing.html", status_code=404)
        if drive_is_pending(invoice.drive_error):
            return redirect(f"/invoices/{invoice_id}?m=drive_started")
        invoice.drive_error = mark_drive_pending()
        invoice.updated_at = utcnow()
        db.commit()
    threading.Thread(target=_save_drive, args=(invoice_id,), daemon=True).start()
    return redirect(f"/invoices/{invoice_id}?m=drive_started")


def load_contract(db, contract_id: int) -> Contract | None:
    return db.scalar(
        select(Contract)
        .where(Contract.id == contract_id)
        .options(
            selectinload(Contract.customer),
            selectinload(Contract.terms).selectinload(ContractTerm.payments),
        )
    )


def read_term(form: FormView):
    try:
        amount = parse_int(form.get("monthly_amount"))
    except ValueError:
        amount = 0
    try:
        duration = parse_int(form.get("duration"))
    except ValueError:
        duration = 0
    currency = form.get("currency")
    if currency not in ("rial", "toman"):
        currency = "rial"
    raw_start = form.get("start_date")
    start = None
    if raw_start:
        try:
            start = date.fromisoformat(raw_start)
        except ValueError:
            start = None
    values = SimpleNamespace(
        monthly_amount=form.get("monthly_amount"),
        duration=form.get("duration") or "12",
        currency=currency,
        start_date=start.isoformat() if start else "",
    )
    if amount < 1 or duration < 1 or duration > 60:
        return None, values, "term_invalid"
    if start is None:
        return None, values, "date_invalid"
    return (amount, duration, start, currency), values, ""


def contract_cards(contracts, today: date, locale: str) -> list[dict]:
    cards = []
    for contract in contracts:
        terms = list(contract.terms)
        if not terms:
            continue
        active = next((term for term in reversed(terms) if term.status == "active"), None)
        current = active or terms[-1]
        months = term_months(current.start_date, current.duration)
        paid = {(payment.year, payment.month) for payment in current.payments}
        total, paid_sum, balance = contract_balance(
            current.monthly_amount,
            current.duration,
            [payment.amount for payment in current.payments],
        )
        focus = focus_period(months, today)
        cards.append({
            "id": contract.id,
            "name": contract.customer.name,
            "phone": contract.customer.phone,
            "tone": tile_tone(months, paid, today),
            "status": current.status,
            "start": current.start_date,
            "duration": current.duration,
            "monthly": current.monthly_amount,
            "currency": current.currency,
            "total": total,
            "paid": paid_sum,
            "balance": balance,
            "focus": format_period(focus[0], focus[1], locale) if focus else "",
            "earlier": len(terms) - 1,
        })
    return cards


def present_term(term: ContractTerm, today: date, locale: str) -> dict:
    payments = {(payment.year, payment.month): payment for payment in term.payments}
    total, paid, balance = contract_balance(
        term.monthly_amount,
        term.duration,
        [payment.amount for payment in term.payments],
    )
    return {
        "term": term,
        "rows": statement_rows(term.start_date, term.duration, term.monthly_amount, payments, today, locale),
        "total": total,
        "paid": paid,
        "balance": balance,
        "edit": SimpleNamespace(
            monthly_amount=format_number(term.monthly_amount, locale),
            duration=str(term.duration),
            currency=term.currency,
            start_date=term.start_date.isoformat(),
        ),
    }


def contract_page(request: Request, contract: Contract, errors: list[str] | None = None, draft=None):
    locale = request.state.user.locale
    today = date.today()
    if draft is None:
        draft = SimpleNamespace(monthly_amount="", duration="12", currency="rial", start_date=today.isoformat())
    views = [present_term(term, today, locale) for term in reversed(list(contract.terms))]
    return html(
        request,
        "contract_detail.html",
        contract=contract,
        views=views,
        has_active=any(term.status == "active" for term in contract.terms),
        errors=errors or [],
        values=draft,
        today=today.isoformat(),
    )


def blank_term_values():
    return SimpleNamespace(monthly_amount="", duration="12", currency="rial", start_date=date.today().isoformat())


@app.get("/contracts")
def contract_list(request: Request):
    with session() as db:
        rows = db.scalars(
            select(Contract)
            .options(
                selectinload(Contract.customer),
                selectinload(Contract.terms).selectinload(ContractTerm.payments),
            )
            .order_by(Contract.id.desc())
        ).unique().all()
        return html(request, "contracts.html", cards=contract_cards(rows, date.today(), request.state.user.locale))


@app.get("/contracts/new")
def new_contract(request: Request):
    with session() as db:
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        return html(
            request,
            "contract_form.html",
            customers=customers,
            selected_customer=0,
            values=blank_term_values(),
            errors=[],
        )


@app.post("/contracts")
def create_contract(request: Request):
    form = read_form(request)
    parsed, values, error = read_term(form)
    try:
        customer_id = int(form.get("customer_id") or "0")
    except ValueError:
        customer_id = 0
    with session() as db:
        locale = request.state.user.locale
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        customer = db.get(Customer, customer_id)
        if customer is None:
            return html(
                request,
                "contract_form.html",
                customers=customers,
                selected_customer=0,
                values=values,
                errors=[translate(locale, "customer_required")],
            )
        existing = db.scalar(select(Contract).where(Contract.customer_id == customer.id))
        if existing is not None:
            return redirect(f"/contracts/{existing.id}?m=contract_exists")
        if error or parsed is None:
            return html(
                request,
                "contract_form.html",
                customers=customers,
                selected_customer=customer.id,
                values=values,
                errors=[translate(locale, error or "term_invalid")],
            )
        amount, duration, start, currency = parsed
        now = utcnow()
        contract = Contract(customer_id=customer.id, created_at=now, updated_at=now)
        contract.terms.append(ContractTerm(
            monthly_amount=amount,
            duration=duration,
            start_date=start,
            currency=currency,
            status="active",
            created_at=now,
        ))
        db.add(contract)
        db.commit()
        db.refresh(contract)
        return redirect(f"/contracts/{contract.id}?m=saved")


@app.get("/contracts/{contract_id}")
def contract_detail(request: Request, contract_id: int):
    with session() as db:
        contract = load_contract(db, contract_id)
        if contract is None:
            return html(request, "missing.html", status_code=404)
        return contract_page(request, contract)


@app.post("/contracts/{contract_id}/terms")
def add_contract_term(request: Request, contract_id: int):
    form = read_form(request)
    parsed, values, error = read_term(form)
    with session() as db:
        contract = load_contract(db, contract_id)
        if contract is None:
            return html(request, "missing.html", status_code=404)
        locale = request.state.user.locale
        if any(term.status == "active" for term in contract.terms):
            return redirect(f"/contracts/{contract_id}?m=term_active")
        if error or parsed is None:
            return contract_page(request, contract, [translate(locale, error or "term_invalid")], values)
        amount, duration, start, currency = parsed
        now = utcnow()
        db.add(ContractTerm(
            contract_id=contract.id,
            monthly_amount=amount,
            duration=duration,
            start_date=start,
            currency=currency,
            status="active",
            created_at=now,
        ))
        contract.updated_at = now
        db.commit()
        return redirect(f"/contracts/{contract_id}?m=saved")


@app.post("/contracts/{contract_id}/terms/{term_id}")
def edit_contract_term(request: Request, contract_id: int, term_id: int):
    form = read_form(request)
    parsed, _values, error = read_term(form)
    with session() as db:
        contract = load_contract(db, contract_id)
        if contract is None:
            return html(request, "missing.html", status_code=404)
        term = next((item for item in contract.terms if item.id == term_id), None)
        if term is None:
            return html(request, "missing.html", status_code=404)
        if term.payments:
            return redirect(f"/contracts/{contract_id}?m=term_locked")
        if error or parsed is None:
            return redirect(f"/contracts/{contract_id}?m={error or 'term_invalid'}")
        amount, duration, start, currency = parsed
        term.monthly_amount = amount
        term.duration = duration
        term.start_date = start
        term.currency = currency
        contract.updated_at = utcnow()
        db.commit()
        return redirect(f"/contracts/{contract_id}?m=saved")


@app.post("/contracts/{contract_id}/terms/{term_id}/status")
def contract_term_status(request: Request, contract_id: int, term_id: int):
    form = read_form(request)
    status = form.get("status")
    if status not in ("active", "completed"):
        return redirect(f"/contracts/{contract_id}")
    with session() as db:
        contract = load_contract(db, contract_id)
        if contract is None:
            return html(request, "missing.html", status_code=404)
        term = next((item for item in contract.terms if item.id == term_id), None)
        if term is None:
            return html(request, "missing.html", status_code=404)
        if status == "active" and any(item.status == "active" and item.id != term.id for item in contract.terms):
            return redirect(f"/contracts/{contract_id}?m=term_active")
        term.status = status
        contract.updated_at = utcnow()
        db.commit()
        return redirect(f"/contracts/{contract_id}?m=saved")


@app.post("/contracts/{contract_id}/terms/{term_id}/payments")
def contract_payments(request: Request, contract_id: int, term_id: int):
    form = read_form(request)
    with session() as db:
        contract = load_contract(db, contract_id)
        if contract is None:
            return html(request, "missing.html", status_code=404)
        term = next((item for item in contract.terms if item.id == term_id), None)
        if term is None:
            return html(request, "missing.html", status_code=404)
        schedule = {f"{year}-{month:02d}": (year, month) for year, month in term_months(term.start_date, term.duration)}
        wanted: dict[tuple[int, int], date] = {}
        for key in form.getlist("paid"):
            period = schedule.get(key)
            if period is None:
                continue
            raw = form.get(f"paid_on_{key}")
            if not raw:
                paid_on = date.today()
            else:
                try:
                    paid_on = date.fromisoformat(raw)
                except ValueError:
                    return redirect(f"/contracts/{contract_id}?m=date_invalid")
            wanted[period] = paid_on
        existing = {(payment.year, payment.month): payment for payment in list(term.payments)}
        for period, payment in existing.items():
            if period not in wanted:
                db.delete(payment)
        for period, paid_on in wanted.items():
            payment = existing.get(period)
            if payment is None:
                db.add(ContractPayment(
                    term_id=term.id,
                    year=period[0],
                    month=period[1],
                    paid_on=paid_on,
                    amount=term.monthly_amount,
                ))
            else:
                payment.paid_on = paid_on
                payment.amount = term.monthly_amount
        contract.updated_at = utcnow()
        db.commit()
        return redirect(f"/contracts/{contract_id}?m=saved")


@app.get("/customers")
def customer_list(request: Request):
    with session() as db:
        customers = db.scalars(select(Customer).order_by(Customer.name)).all()
        return html(request, "customers.html", customers=customers)


@app.get("/customers/new")
def new_customer(request: Request):
    return html(request, "customer_form.html", customer=None, values=customer_values(SimpleNamespace()), errors=[])


@app.post("/customers")
def create_customer(request: Request):
    return save_customer(request, None)


@app.get("/customers/{customer_id}/edit")
def edit_customer(request: Request, customer_id: int):
    with session() as db:
        customer = db.get(Customer, customer_id)
        if customer is None:
            return html(request, "missing.html", status_code=404)
        return html(request, "customer_form.html", customer=customer, values=customer_values(customer), errors=[])


@app.post("/customers/{customer_id}/edit")
def update_customer(request: Request, customer_id: int):
    return save_customer(request, customer_id)


def save_customer(request: Request, customer_id: int | None):
    form = read_form(request)
    values = {
        "name": form.get("name").strip()[:200],
        "phone": form.get("phone").strip()[:80],
        "address": form.get("address").strip()[:2000],
        "tax_id": form.get("tax_id").strip()[:64],
        "bale_id": form.get("bale_id").strip()[:64],
        "telegram_id": form.get("telegram_id").strip()[:64],
    }
    locale = request.state.user.locale
    errors = []
    if not values["name"]:
        errors.append(translate(locale, "customer_required"))
    with session() as db:
        customer = db.get(Customer, customer_id) if customer_id else None
        if customer_id and customer is None:
            return html(request, "missing.html", status_code=404)
        if errors:
            return html(request, "customer_form.html", customer=customer, values=values, errors=errors)
        if customer is None:
            customer = Customer(created_at=utcnow(), **values)
            db.add(customer)
        else:
            for key, value in values.items():
                setattr(customer, key, value)
        db.commit()
    return redirect("/customers?m=saved")


@app.post("/customers/{customer_id}/delete")
def delete_customer(request: Request, customer_id: int):
    with session() as db:
        customer = db.get(Customer, customer_id)
        if customer is None:
            return html(request, "missing.html", status_code=404)
        used = db.scalar(select(Invoice.id).where(Invoice.customer_id == customer.id).limit(1))
        if used:
            return redirect("/customers?m=customer_in_use")
        db.delete(customer)
        db.commit()
    return redirect("/customers?m=deleted")


@app.get("/summary")
def summary(request: Request, start: str = "", end: str = ""):
    locale = request.state.user.locale
    default_start, default_end = default_period(date.today(), locale)
    try:
        start_date = date.fromisoformat(start) if start else default_start
        end_date = date.fromisoformat(end) if end else default_end
    except ValueError:
        start_date, end_date = default_start, default_end
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    with session() as db:
        rows, buckets = book_rows(db, start_date, end_date)
    return html(request, "summary.html", rows=rows, buckets=buckets, start=start_date.isoformat(), end=end_date.isoformat())


@app.get("/summary.csv")
def summary_csv(request: Request, start: str = "", end: str = ""):
    locale = request.state.user.locale
    default_start, default_end = default_period(date.today(), locale)
    try:
        start_date = date.fromisoformat(start) if start else default_start
        end_date = date.fromisoformat(end) if end else default_end
    except ValueError:
        start_date, end_date = default_start, default_end
    with session() as db:
        rows, buckets = book_rows(db, start_date, end_date)
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["number", "customer", "currency", "issue_date", "issued", "paid", "outstanding"])
    for row in rows:
        writer.writerow([
            row["number"],
            _csv_text(row["customer"]),
            row["currency"],
            row["issue_date"].isoformat(),
            row["total"],
            row["paid"],
            row["outstanding"],
        ])
    writer.writerow([])
    writer.writerow(["currency", "issued", "paid", "outstanding"])
    for currency, bucket in buckets.items():
        writer.writerow([currency, bucket["issued"], bucket["paid"], bucket["outstanding"]])
    payload = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return Response(payload, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=summary.csv"})


@app.get("/send-debt/{customer_id}")
def send_debt_to_admin(request: Request, customer_id: int):
    denied = require_admin(request)
    if denied:
        return denied
    locale = request.state.user.locale
    with session() as db:
        customer = db.get(Customer, customer_id)
        if customer is None:
            return html(request, "missing.html", status_code=404)
        drive = load_drive(db)
        lines = [f"{translate(locale, 'debt_of')} {customer.name}"]
        total = 0
        invoices = db.scalars(
            select(Invoice).where(
                Invoice.customer_id == customer.id,
                Invoice.archived.is_(False),
                Invoice.doc_type == "invoice",
            )
        ).all()
        for inv in invoices:
            due = present(inv)["outstanding"]
            if due <= 0:
                continue
            total += due
            lines.append(f"{translate(locale, 'invoice_no')} {inv.number}: {format_number(due, locale)}")
        contracts = db.scalars(select(Contract).where(Contract.customer_id == customer.id)).all()
        for contract in contracts:
            term = next((item for item in reversed(contract.terms) if item.status == "active"), None)
            if term is None:
                continue
            _gross, _paid, remain = contract_balance(
                term.monthly_amount,
                term.duration,
                [payment.amount for payment in term.payments],
            )
            if remain <= 0:
                continue
            total += remain
            lines.append(f"{translate(locale, 'contract_word')}: {format_number(remain, locale)}")

        if total <= 0:
            return redirect(f"/invoices?customer={customer.id}&m=debt_none")
        lines.append(f"{translate(locale, 'total')}: {format_number(total, locale)} {translate(locale, 'toman')}")
        text = "\n".join(lines)
        sent = False
        for channel, key in (("bale", "admin_bale_id"), ("telegram", "admin_telegram_id")):
            chat_id = (drive.get(key) or "").strip()
            if not chat_id:
                continue
            ok, error = send_text(channel, chat_id, text, channel_token(db, channel))
            if ok:
                sent = True
            else:
                log.info("admin debt send failed on %s: %s", channel, error)
        return redirect(f"/invoices?customer={customer.id}&m={'send_ok' if sent else 'send_failed'}")


def _csv_text(value: str) -> str:
    if value[:1] in ("=", "+", "@"):
        return "'" + value
    return value


@app.get("/users")
def user_list(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    with session() as db:
        people = db.scalars(select(User).order_by(User.username)).all()
        return html(request, "users.html", people=people)


@app.get("/users/new")
def new_user(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    return html(request, "user_form.html", person=None, values={"username": "", "role": "user", "locale": "fa"}, errors=[])


@app.post("/users")
def create_user(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    return save_user(request, None)


@app.get("/users/{user_id}/edit")
def edit_user(request: Request, user_id: int):
    denied = require_admin(request)
    if denied:
        return denied
    with session() as db:
        person = db.get(User, user_id)
        if person is None:
            return html(request, "missing.html", status_code=404)
        values = {"username": person.username, "role": person.role, "locale": person.locale}
        return html(request, "user_form.html", person=person, values=values, errors=[])


@app.post("/users/{user_id}/edit")
def update_user(request: Request, user_id: int):
    denied = require_admin(request)
    if denied:
        return denied
    return save_user(request, user_id)


def save_user(request: Request, user_id: int | None):
    form = read_form(request)
    locale = request.state.user.locale
    values = {
        "username": form.get("username").strip()[:80],
        "role": form.get("role") if form.get("role") in ("admin", "user") else "user",
        "locale": form.get("locale") if form.get("locale") in ("fa", "en") else "fa",
    }
    password = form.get("password")
    errors = []
    if not values["username"]:
        errors.append(translate(locale, "username_taken"))
    if user_id is None and not password:
        errors.append(translate(locale, "password_required"))
    with session() as db:
        person = db.get(User, user_id) if user_id else None
        if user_id and person is None:
            return html(request, "missing.html", status_code=404)
        if person and person.role == "admin" and values["role"] != "admin" and admin_count(db) <= 1:
            errors.append(translate(locale, "cannot_delete_last_admin"))
        if errors:
            return html(request, "user_form.html", person=person, values=values, errors=errors)
        existing_id = person.id if person is not None else None
        if person is None:
            person = User(username=values["username"], password_hash=hash_password(password), role=values["role"], locale=values["locale"], created_at=utcnow())
            db.add(person)
        else:
            person.username = values["username"]
            person.role = values["role"]
            person.locale = values["locale"]
            if password:
                person.password_hash = hash_password(password)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            errors.append(translate(locale, "username_taken"))
            ghost = SimpleNamespace(id=existing_id) if existing_id else None
            return html(request, "user_form.html", person=ghost, values=values, errors=errors)
    return redirect("/users?m=saved")


@app.post("/users/{user_id}/delete")
def delete_user(request: Request, user_id: int):
    denied = require_admin(request)
    if denied:
        return denied
    if user_id == request.state.user.id:
        return redirect("/users?m=cannot_delete_self")
    with session() as db:
        person = db.get(User, user_id)
        if person is None:
            return html(request, "missing.html", status_code=404)
        if person.role == "admin" and admin_count(db) <= 1:
            return redirect("/users?m=cannot_delete_last_admin")
        db.execute(delete(SessionRow).where(SessionRow.user_id == person.id))
        db.delete(person)
        db.commit()
    return redirect("/users?m=deleted")


@app.get("/settings")
def settings_form(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    with session() as db:
        issuer = get_issuer(db)
        drive = public_view(load_drive(db))
        return html(request, "settings.html", issuer=issuer, drive=drive, error="")


@app.post("/settings")
def settings_save(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    form = read_form(request)
    action = form.get("action") or "save"
    locale = request.state.user.locale
    with session() as db:
        issuer = get_issuer(db)
        issuer.company = form.get("company").strip()[:200]
        issuer.contact_name = form.get("contact_name").strip()[:200]
        issuer.phone = form.get("issuer_phone").strip()[:80]
        issuer.address = form.get("issuer_address").strip()[:2000]
        issuer.tax_id = form.get("issuer_tax_id").strip()[:64]
        current = load_drive(db)
        try:
            updated = apply_form(current, form)
        except (ValueError, json.JSONDecodeError):
            db.commit()
            return html(
                request,
                "settings.html",
                issuer=issuer,
                drive=public_view(current),
                error=translate(locale, "invalid_service_account"),
            )
        save_drive(db, updated)
        db.commit()
        if action == "test":
            try:
                name = test_connection(db)
                fresh = load_drive(db)
                fresh["last_test"] = name
                save_drive(db, fresh)
            except Exception as exc:
                return html(
                    request,
                    "settings.html",
                    issuer=issuer,
                    drive=public_view(load_drive(db)),
                    error=redact(str(exc)) or translate(locale, "test_failed"),
                )
            return redirect("/settings?m=test_ok")
        if action == "connect":
            try:
                target = authorization_url(updated, request.state.user.id)
            except RuntimeError:
                return html(request, "settings.html", issuer=issuer, drive=public_view(updated), error=translate(locale, "oauth_required"))
            return redirect(target)
    return redirect("/settings?m=saved")


@app.get("/settings/drive/oauth/callback")
def oauth_callback(request: Request):
    denied = require_admin(request)
    if denied:
        return denied
    if request.query_params.get("error") or not parse_state(request.query_params.get("state", ""), request.state.user.id):
        return redirect("/settings?m=test_failed")
    with session() as db:
        data = load_drive(db)
        try:
            exchange_code(db, data, request.query_params.get("code", ""))
        except Exception:
            return redirect("/settings?m=test_failed")
    return redirect("/settings?m=connected_msg")
