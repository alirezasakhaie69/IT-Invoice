import hashlib
import hmac
import json
import logging
import os
import secrets
import tempfile
import time
import urllib.parse

from app.config import app_base_url, app_secret
from app.crypto import decrypt_json, encrypt_json, redact
from app.dates import to_jalali
from app.models import DriveSetting

log = logging.getLogger("invoice")

SCOPE = "https://www.googleapis.com/auth/drive"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
DRIVE = "https://www.googleapis.com/drive/v3"

DEFAULTS = {
    "mode": "oauth",
    "folder_template": "Invoices/{year}",
    "auto_save_on_send": False,
    "oauth_client_id": "",
    "oauth_client_secret": "",
    "refresh_token": "",
    "access_token": "",
    "access_expires": 0,
    "service_account_json": "",
    "folder_id": "",
    "shared_drive_id": "",
    "sa_access_token": "",
    "sa_access_expires": 0,
}


def redirect_uri() -> str:
    return app_base_url() + "/settings/drive/oauth/callback"


def load_drive(db) -> dict:
    row = db.get(DriveSetting, 1)
    data = dict(DEFAULTS)
    if row is not None and row.ciphertext:
        data.update(decrypt_json(row.ciphertext))
    return data


def save_drive(db, data: dict) -> None:
    blob = encrypt_json(data)
    row = db.get(DriveSetting, 1)
    if row is None:
        db.add(DriveSetting(id=1, ciphertext=blob))
    else:
        row.ciphertext = blob
    db.commit()


def root_folder_id(data: dict) -> str:
    """Service-account uploads start in this folder. Older settings stored a Shared Drive id."""
    return (data.get("folder_id") or data.get("shared_drive_id") or "").strip()


def public_view(data: dict) -> dict:
    return {
        "mode": data.get("mode") or "oauth",
        "folder_template": data.get("folder_template") or "Invoices/{year}",
        "auto_save_on_send": bool(data.get("auto_save_on_send")),
        "oauth_client_id": data.get("oauth_client_id") or "",
        "has_secret": bool(data.get("oauth_client_secret")),
        "connected": bool(data.get("refresh_token")),
        "folder_id": root_folder_id(data),
        "has_service_account": bool(data.get("service_account_json")),
        "has_bale_token": bool(data.get("bale_bot_token")),
        "has_telegram_token": bool(data.get("telegram_bot_token")),
        "redirect_uri": redirect_uri(),
        "last_test": data.get("last_test") or "",
    }


def apply_form(current: dict, form) -> dict:
    data = dict(current)
    mode = form.get("mode") or "oauth"
    data["mode"] = mode if mode in ("oauth", "service_account") else "oauth"
    data["folder_template"] = (form.get("folder_template") or "").strip()[:200] or "Invoices/{year}"
    data["auto_save_on_send"] = form.get("auto_save") == "on"
    data["folder_id"] = (form.get("folder_id") or "").strip()[:128]
    data["shared_drive_id"] = ""
    client_id = (form.get("oauth_client_id") or "").strip()
    if client_id and client_id != data.get("oauth_client_id"):
        data["oauth_client_id"] = client_id
        data["refresh_token"] = ""
        data["access_token"] = ""
        data["access_expires"] = 0
    elif client_id:
        data["oauth_client_id"] = client_id
    secret = (form.get("oauth_client_secret") or "").strip()
    if secret:
        data["oauth_client_secret"] = secret
    bale = (form.get("bale_bot_token") or "").strip()
    if bale:
        data["bale_bot_token"] = bale
    telegram = (form.get("telegram_bot_token") or "").strip()
    if telegram:
        data["telegram_bot_token"] = telegram
    raw_json = (form.get("service_account_json") or "").strip()
    if raw_json:
        if len(raw_json) > 100_000:
            raise ValueError("invalid service account")
        parsed = json.loads(raw_json)
        if not isinstance(parsed, dict) or "client_email" not in parsed or "private_key" not in parsed:
            raise ValueError("invalid service account")
        data["service_account_json"] = json.dumps(parsed)
        data["sa_access_token"] = ""
        data["sa_access_expires"] = 0
    return data


def make_state(user_id: int) -> str:
    payload = f"{user_id}.{int(time.time())}"
    mac = hmac.new(app_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def parse_state(state: str, user_id: int, max_age: int = 600) -> bool:
    parts = (state or "").split(".")
    if len(parts) != 3:
        return False
    uid, ts, mac = parts
    if not uid.isdigit() or not ts.isdigit() or int(uid) != user_id:
        return False
    if abs(time.time() - int(ts)) > max_age:
        return False
    expected = hmac.new(app_secret().encode(), f"{uid}.{ts}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, mac)


def mark_drive_pending() -> str:
    return f"pending:{int(time.time())}"


def drive_is_pending(value: str, now: float | None = None) -> bool:
    now = time.time() if now is None else now
    if not (value or "").startswith("pending:"):
        return False
    try:
        started = int(value.split(":", 1)[1])
    except ValueError:
        return False
    return 0 <= now - started < 600


def _post_form(url: str, fields: dict) -> dict:
    from app.send import exchange

    body = urllib.parse.urlencode(fields).encode()
    status, raw = exchange(
        url,
        method="POST",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        prefer_ipv4=True,
    )
    try:
        parsed = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if status != 200:
        message = parsed.get("error_description") or parsed.get("error") or f"HTTP {status}"
        raise RuntimeError(redact(str(message)))
    return parsed


class _TokenResponse:
    def __init__(self, status: int, data: bytes):
        self.status = status
        self.data = data
        self.headers: dict[str, str] = {}


class _TokenRequest:
    """google-auth transport that keeps the token call on the same IPv4 path as the upload."""

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        from app.send import exchange

        payload = body.encode() if isinstance(body, str) else body
        status, raw = exchange(
            url,
            method=method,
            data=payload,
            headers=dict(headers or {}),
            timeout=timeout or 60,
            prefer_ipv4=True,
        )
        return _TokenResponse(status, raw)


def _service_account_credentials(info: dict):
    from google.oauth2 import service_account

    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "service-account.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(info, handle)
        return service_account.Credentials.from_service_account_file(path, scopes=[SCOPE])
    finally:
        if os.path.exists(path):
            os.remove(path)
        os.rmdir(directory)


def _bearer(db, data: dict) -> str:
    now = time.time()
    if data["mode"] == "service_account":
        if not data["service_account_json"] or not root_folder_id(data):
            raise RuntimeError("Service account JSON and a Drive folder id are required.")
        if data["sa_access_token"] and data["sa_access_expires"] > now + 60:
            return data["sa_access_token"]
        info = json.loads(data["service_account_json"])
        credentials = _service_account_credentials(info)
        credentials.refresh(_TokenRequest())
        if not credentials.token:
            raise RuntimeError("Google did not return an access token.")
        remaining = 3600
        if credentials.expiry is not None:
            remaining = max(60, int(credentials.expiry.timestamp() - now))
        data["sa_access_token"] = credentials.token
        data["sa_access_expires"] = int(now + remaining)
        save_drive(db, data)
        return data["sa_access_token"]
    return _oauth_access_token(db, data)


def _google_connection_revoked(exc: Exception) -> bool:
    text = str(exc).lower()
    return "expired or revoked" in text or "invalid_grant" in text


def _forget_google_connection(db, data: dict) -> None:
    data["refresh_token"] = ""
    data["access_token"] = ""
    data["access_expires"] = 0
    save_drive(db, data)


def _oauth_access_token(db, data: dict, language: str = "en") -> str:
    from app.i18n import translate

    now = time.time()
    if not data.get("refresh_token") or not data.get("oauth_client_id") or not data.get("oauth_client_secret"):
        raise RuntimeError("Connect a Google account first.")
    if data.get("access_token") and data.get("access_expires", 0) > now + 60:
        return data["access_token"]
    try:
        token = _post_form(TOKEN_URL, {
            "client_id": data["oauth_client_id"],
            "client_secret": data["oauth_client_secret"],
            "refresh_token": data["refresh_token"],
            "grant_type": "refresh_token",
        })
    except RuntimeError as exc:
        if not _google_connection_revoked(exc):
            raise
        _forget_google_connection(db, data)
        raise RuntimeError(translate(language, "drive_reconnect")) from None
    data["access_token"] = token["access_token"]
    data["access_expires"] = int(now + int(token.get("expires_in", 3600)))
    save_drive(db, data)
    return data["access_token"]


def authorization_url(data: dict, user_id: int) -> str:
    if not data["oauth_client_id"] or not data["oauth_client_secret"]:
        raise RuntimeError("oauth")
    query = urllib.parse.urlencode({
        "client_id": data["oauth_client_id"],
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(user_id),
    })
    return f"{AUTH_URL}?{query}"


def exchange_code(db, data: dict, code: str) -> None:
    token = _post_form(TOKEN_URL, {
        "code": code,
        "client_id": data["oauth_client_id"],
        "client_secret": data["oauth_client_secret"],
        "redirect_uri": redirect_uri(),
        "grant_type": "authorization_code",
    })
    if not token.get("refresh_token") and not data.get("refresh_token"):
        raise RuntimeError("Google did not return a refresh token.")
    if token.get("refresh_token"):
        data["refresh_token"] = token["refresh_token"]
    data["access_token"] = token.get("access_token", "")
    data["access_expires"] = int(time.time() + int(token.get("expires_in", 3600)))
    save_drive(db, data)


class DriveCallError(RuntimeError):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _api(method: str, url: str, token: str, body: bytes | None = None, content_type: str | None = None):
    from app.send import exchange

    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    status, raw = exchange(url, method=method, data=body, headers=headers, timeout=120, prefer_ipv4=True)
    try:
        parsed = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        parsed = {}
    if status not in (200, 201):
        error = parsed.get("error")
        if isinstance(error, dict):
            message = error.get("message") or str(error)
        else:
            message = str(error or f"HTTP {status}")
        raise DriveCallError(redact(message), status)
    return parsed


def _api_my_drive(method: str, url: str, token: str, body: bytes | None = None, content_type: str | None = None):
    """Call Drive for a personal folder. Retry with shared-drive support only when the parent is hidden."""
    try:
        return _api(method, url, token, body, content_type)
    except DriveCallError as exc:
        if exc.status != 404:
            raise
        join = "&" if "?" in url else "?"
        return _api(method, f"{url}{join}supportsAllDrives=true", token, body, content_type)


def _quote_query(name: str) -> str:
    return name.replace("\\", "\\\\").replace("'", "\\'")


def _find_folder(token: str, name: str, parent: str) -> str | None:
    # A parent query is enough. corpora=allDrives walks every shared drive and the read times out.
    query = (
        f"name = '{_quote_query(name)}' and mimeType = 'application/vnd.google-apps.folder' "
        f"and '{parent}' in parents and trashed = false"
    )
    params = {
        "q": query,
        "fields": "files(id,name)",
        "pageSize": "5",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    found = _api("GET", f"{DRIVE}/files?{urllib.parse.urlencode(params)}", token)
    files = found.get("files") or []
    return files[0]["id"] if files else None


def _create_folder(token: str, name: str, parent: str) -> str:
    body = json.dumps({
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent],
    }).encode()
    created = _api_my_drive("POST", f"{DRIVE}/files", token, body, "application/json")
    return created["id"]


def _under_template(token: str, data: dict, invoice, root: str) -> str:
    template = (data.get("folder_template") or "Invoices/{year}").strip()
    if invoice.language == "fa":
        year, month, _day = to_jalali(invoice.issue_date)
    else:
        year, month = invoice.issue_date.year, invoice.issue_date.month
    rendered = template.replace("{year}", str(year)).replace("{month}", f"{month:02d}")
    parent = root
    for part in [piece for piece in rendered.split("/") if piece.strip()]:
        found = _find_folder(token, part.strip(), parent)
        parent = found or _create_folder(token, part.strip(), parent)
    return parent


def _destination(token: str, data: dict, invoice) -> str:
    # A service account has no Drive storage, so it cannot create the year folders.
    # The file is placed directly in the personal folder. The connected Google account
    # creates those folders if Google rejects the service account.
    if data["mode"] == "service_account":
        return root_folder_id(data)
    return _under_template(token, data, invoice, "root")


def _service_account_has_no_quota(exc: Exception) -> bool:
    text = str(exc).lower()
    return "storage quota" in text or "storagequotaexceeded" in text


def test_connection(db) -> str:
    data = load_drive(db)
    token = _bearer(db, data)
    data = load_drive(db)
    if data["mode"] == "service_account":
        folder_id = urllib.parse.quote(root_folder_id(data))
        found = _api(
            "GET",
            f"{DRIVE}/files/{folder_id}?fields=id,name,mimeType&supportsAllDrives=true",
            token,
        )
        if found.get("mimeType") != "application/vnd.google-apps.folder":
            raise RuntimeError("That Drive id is not a folder.")
        return found.get("name") or root_folder_id(data)
    about = _api("GET", f"{DRIVE}/about?fields=user", token)
    user = about.get("user") or {}
    return user.get("emailAddress") or "ok"


def _upload_file(token: str, parent: str, filename: str, payload: bytes) -> str:
    boundary = "itinvoice" + secrets.token_hex(8)
    meta = json.dumps({"name": filename, "parents": [parent], "mimeType": "application/pdf"}).encode()
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        meta,
        b"\r\n",
        f"--{boundary}\r\nContent-Type: application/pdf\r\n\r\n".encode(),
        payload,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    created = _api_my_drive(
        "POST",
        "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
        token,
        body,
        f"multipart/related; boundary={boundary}",
    )
    return created.get("id", "")


def upload_pdf(db, invoice, payload: bytes, filename: str) -> str:
    from app.i18n import translate

    data = load_drive(db)
    if data.get("mode") == "service_account":
        folder_id = root_folder_id(data)
        service_token = _bearer(db, data)
        data = load_drive(db)
        try:
            return _upload_file(service_token, folder_id, filename, payload)
        except DriveCallError as exc:
            if not _service_account_has_no_quota(exc):
                raise
            if not data.get("refresh_token"):
                language = getattr(invoice, "language", None) or "fa"
                raise RuntimeError(translate(language, "drive_sa_quota")) from None
            language = getattr(invoice, "language", None) or "fa"
            user_token = _oauth_access_token(db, data, language)
            data = load_drive(db)
            parent = _under_template(user_token, data, invoice, folder_id)
            return _upload_file(user_token, parent, filename, payload)
    token = _bearer(db, data)
    data = load_drive(db)
    return _upload_file(token, _destination(token, data, invoice), filename, payload)


def auto_save(db, invoice, payload: bytes, filename: str) -> None:
    data = load_drive(db)
    if not data.get("auto_save_on_send"):
        return
    try:
        invoice.drive_file_id = upload_pdf(db, invoice, payload, filename)
        invoice.drive_error = ""
    except Exception as exc:
        invoice.drive_error = redact(str(exc))
        log.info("drive auto-save failed")
    db.commit()
