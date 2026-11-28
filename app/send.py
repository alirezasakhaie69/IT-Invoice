import http.client
import json
import logging
import secrets
import socket
import urllib.error
import urllib.request
from pathlib import Path

from sqlalchemy import update

from app.auth import utcnow
from app.config import bale_base, bale_token, data_dir, telegram_base, telegram_proxy, telegram_token
from app.crypto import redact
from app.format import localize_digits
from app.i18n import translate
from app.models import Invoice, SendLog
from app.pdf import pdf_filename, render_invoice_pdf

log = logging.getLogger("invoice")

# WireGuard that carries only Google addresses often cannot return the ICMP message
# that would shrink a packet. A full-size segment is dropped, a short request still
# succeeds, and the PDF upload waits until the read times out. 1100 bytes fits a
# tunnel MTU of 1280 after the TCP and IP headers.
_TUNNEL_MSS = 1100


def _limit_segment_size(sock: socket.socket) -> None:
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_MAXSEG, _TUNNEL_MSS)
    except OSError:
        return


def connect_prefer_ipv4(host: str, port: int, timeout):
    """Try IPv4 before IPv6. A dead IPv6 route can stall each Google call for minutes."""
    read_timeout = 60 if timeout is None or timeout is socket._GLOBAL_DEFAULT_TIMEOUT else timeout
    connect_timeout = min(8, read_timeout)
    errors: list[OSError] = []
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            infos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except socket.gaierror as exc:
            errors.append(exc)
            continue
        for fam, socktype, proto, _canon, sockaddr in infos:
            sock = socket.socket(fam, socktype, proto)
            sock.settimeout(connect_timeout)
            _limit_segment_size(sock)
            try:
                sock.connect(sockaddr)
                sock.settimeout(read_timeout)
                return sock
            except OSError as exc:
                errors.append(exc)
                sock.close()
    raise OSError(errors[-1] if errors else f"could not connect to {host}")


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        import errno
        import sys

        sys.audit("http.client.connect", self, self.host, self.port)
        self.sock = connect_prefer_ipv4(self.host, self.port, self.timeout)
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise
        if self._tunnel_host:
            self._tunnel()
        server_hostname = self._tunnel_host or self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_IPv4HTTPSConnection, request, context=self._context)


def exchange(url, *, method="GET", data=None, headers=None, timeout=60, proxy=None, prefer_ipv4=False):
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    elif prefer_ipv4:
        handlers.append(_IPv4HTTPSHandler())
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


def write_pdf(invoice, customer, issuer, transparency: int = 25) -> Path:
    path = pdf_path(invoice.id)
    path.write_bytes(render_invoice_pdf(invoice, customer, issuer, transparency))
    return path


def channel_token(db, channel: str) -> str:
    from app.drive import load_drive

    saved = (load_drive(db).get("bale_bot_token" if channel == "bale" else "telegram_bot_token") or "").strip()
    if saved:
        return saved
    return bale_token() if channel == "bale" else telegram_token()


def deliver(channel: str, chat_id: str, filename: str, payload: bytes, caption: str, token: str) -> tuple[bool, str]:
    if channel == "bale":
        base, proxy = bale_base(), None
    else:
        base, proxy = telegram_base(), telegram_proxy() or None
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


def record_send(db, invoice_id: int, channel: str, ok: bool, error: str) -> None:
    db.add(SendLog(
        invoice_id=invoice_id,
        created_at=utcnow(),
        channel=channel,
        result="ok" if ok else "error",
        error="" if ok else redact(error),
    ))


def send_invoice(db, invoice: Invoice, customer, issuer) -> str:
    locked = db.execute(
        update(Invoice).where(Invoice.id == invoice.id, Invoice.sending.is_(False)).values(sending=True)
    )
    db.commit()
    if locked.rowcount != 1:
        return "send_in_progress"
    channels: list[tuple[str, str]] = []
    try:
        db.refresh(invoice)
        if customer.bale_id.strip():
            channels.append(("bale", customer.bale_id.strip()))
        if customer.telegram_id.strip():
            channels.append(("telegram", customer.telegram_id.strip()))
        if not channels:
            return "no_channel"
        path = write_pdf(invoice, customer, issuer)
        payload = path.read_bytes()
        filename = pdf_filename(invoice, customer.name)
        locale = invoice.language if invoice.language in ("fa", "en") else "fa"
        title_key = "title_invoice" if invoice.doc_type == "invoice" else "title_proforma"
        caption = f"{translate(locale, title_key)} {localize_digits(str(invoice.number), locale)}"
        all_ok = True
        for channel, chat_id in channels:
            ok, error = deliver(channel, chat_id, filename, payload, caption, channel_token(db, channel))
            record_send(db, invoice.id, channel, ok, error)
            if not ok:
                all_ok = False
                log.info("send failed on %s: %s", channel, error)
        if all_ok:
            invoice.delivery = "sent"
        invoice.updated_at = utcnow()
        db.commit()
        if all_ok:
            from app.drive import auto_save

            try:
                auto_save(db, invoice, payload, filename)
            except Exception:
                log.info("drive auto-save failed after send")
        return "send_ok" if all_ok else "send_failed"
    except Exception as exc:
        message = redact(str(exc) or type(exc).__name__)
        targets = channels or [("bale", "")]
        for channel, _chat_id in targets:
            record_send(db, invoice.id, channel, False, message)
        invoice.updated_at = utcnow()
        db.commit()
        log.info("send crashed: %s", type(exc).__name__)
        return "send_failed"
    finally:
        db.execute(update(Invoice).where(Invoice.id == invoice.id).values(sending=False))
        db.commit()

def send_debt_to_admin(text_en: str, text_fa: str, drive) -> tuple[str, str]:
    """ارسال پیام بدهی به مدیر از طریق Bale و/یا Telegram.
    متن پیام قبلاً بر اساس زبان انتخابی کاربر ساخته شده است.
    """
    from app.drive import load_drive
    from app.config import bale_base, telegram_base, telegram_proxy

    # بارگذاری توکن‌ها از DriveSetting (رمزگذاری‌شده)
    bale_token = channel_token(None, "bale")  # اگر db ندارید، از drive استفاده کنید
    tele_token = channel_token(None, "telegram")

    admin_bale = (drive.get("admin_bale_id") or "").strip()
    admin_tele = (drive.get("admin_telegram_id") or "").strip()

    if not admin_bale and not admin_tele:
        return "failed", "debt_no_admin_channel"

    results = []

    # ارسال به Bale
    if admin_bale and bale_token:
        url = f"{bale_base()}/bot{bale_token}/sendMessage"
        payload = json.dumps({"chat_id": admin_bale, "text": text_fa}).encode()
        try:
            status, _ = exchange(
                url,
                method="POST",
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            results.append(200 <= status < 300)
        except Exception:
            results.append(False)

    # ارسال به Telegram
    if admin_tele and tele_token:
        url = f"{telegram_base()}/bot{tele_token}/sendMessage"
        proxy = telegram_proxy() or None
        payload = json.dumps({"chat_id": admin_tele, "text": text_en}).encode()
        try:
            status, _ = exchange(
                url,
                method="POST",
                data=payload,
                headers={"Content-Type": "application/json"},
                timeout=15,
                proxy=proxy,
            )
            results.append(200 <= status < 300)
        except Exception:
            results.append(False)

    if not results:
        return "failed", "debt_no_admin_channel"
    if all(results):
        return "sent", "debt_sent"
    if any(results):
        return "partial", "debt_partial_fail"
    return "failed", "debt_failed"
