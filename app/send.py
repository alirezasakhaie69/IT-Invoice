import json
import logging
import secrets
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import update

from app.auth import utcnow
from app.config import bale_base, bale_token, data_dir, telegram_base, telegram_proxy, telegram_token
from app.crypto import redact
from app.format import format_number
from app.i18n import translate
from app.models import Invoice, SendLog
from app.pdf import pdf_filename, render_invoice_pdf

log = logging.getLogger("invoice")


def exchange(url, *, method="GET", data=None, headers=None, timeout=60, proxy=None):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except urllib.error.URLError as exc:
        raise ConnectionError(redact(str(exc.reason))) from None


def encode_multipart(boundary: str, fields: dict, files: dict) -> bytes:
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    for key, (filename, content, content_type) in files.items():
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks)


def pdf_path(invoice_id: int) -> Path:
    directory = data_dir() / "pdfs"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{invoice_id}.pdf"


def write_pdf(invoice, customer, issuer) -> Path:
    path = pdf_path(invoice.id)
    path.write_bytes(render_invoice_pdf(invoice, customer, issuer))
    return path


def deliver(channel: str, chat_id: str, filename: str, payload: bytes, caption: str) -> tuple[bool, str]:
    if channel == "bale":
        base, token, proxy = bale_base(), bale_token(), None
    else:
        base, token, proxy = telegram_base(), telegram_token(), telegram_proxy() or None
    if not token:
        return False, "Bot token is not configured."
    url = f"{base}/bot{token}/sendDocument"
    boundary = "itinvoice" + secrets.token_hex(8)
    body = encode_multipart(
        boundary,
        {"chat_id": chat_id, "caption": caption},
        {"document": (filename, payload, "application/pdf")},
    )
    try:
        status, raw = exchange(
            url,
            method="POST",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=120,
            proxy=proxy,
        )
    except ConnectionError as exc:
        return False, redact(str(exc))
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False, redact(f"HTTP {status}")
    if status == 200 and parsed.get("ok") is True:
        return True, ""
    return False, redact(str(parsed.get("description") or f"HTTP {status}"))


def send_invoice(db, invoice: Invoice, customer, issuer) -> str:
    locked = db.execute(
        update(Invoice).where(Invoice.id == invoice.id, Invoice.sending.is_(False)).values(sending=True)
    )
    db.commit()
    if locked.rowcount != 1:
        return "send_in_progress"
    try:
        db.refresh(invoice)
        channels = []
        if customer.bale_id.strip():
            channels.append(("bale", customer.bale_id.strip()))
        if customer.telegram_id.strip():
            channels.append(("telegram", customer.telegram_id.strip()))
        if not channels:
            return "no_channel"
        path = write_pdf(invoice, customer, issuer)
        payload = path.read_bytes()
        filename = pdf_filename(invoice)
        locale = invoice.language if invoice.language in ("fa", "en") else "fa"
        title_key = "title_invoice" if invoice.doc_type == "invoice" else "title_proforma"
        caption = f"{translate(locale, title_key)} {format_number(invoice.number, locale)}"
        all_ok = True
        for channel, chat_id in channels:
            ok, error = deliver(channel, chat_id, filename, payload, caption)
            db.add(SendLog(
                invoice_id=invoice.id,
                created_at=utcnow(),
                channel=channel,
                result="ok" if ok else "error",
                error="" if ok else error,
            ))
            if not ok:
                all_ok = False
                log.info("send failed on %s", channel)
        if all_ok:
            invoice.delivery = "sent"
        invoice.updated_at = utcnow()
        db.commit()
        if all_ok:
            from app.drive import auto_save

            auto_save(db, invoice, payload, filename)
        return "send_ok" if all_ok else "send_failed"
    finally:
        db.execute(update(Invoice).where(Invoice.id == invoice.id).values(sending=False))
        db.commit()
