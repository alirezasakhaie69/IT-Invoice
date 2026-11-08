import base64
import json
import os
import re

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from app.config import app_secret

_FERNETS: dict[str, Fernet] = {}


def fernet() -> Fernet:
    secret = app_secret()
    if not secret:
        raise RuntimeError("APP_SECRET_KEY must be set.")
    cached = _FERNETS.get(secret)
    if cached is not None:
        return cached
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b"it-invoice-drive-v1", iterations=120_000)
    key = base64.urlsafe_b64encode(kdf.derive(secret.encode()))
    cached = Fernet(key)
    _FERNETS[secret] = cached
    return cached


def encrypt_json(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False).encode()
    return fernet().encrypt(raw).decode()


def decrypt_json(token: str) -> dict:
    if not token:
        return {}
    try:
        raw = fernet().decrypt(token.encode())
    except InvalidToken as exc:
        raise RuntimeError("Stored settings could not be decrypted.") from exc
    data = json.loads(raw.decode())
    if not isinstance(data, dict):
        raise RuntimeError("Stored settings could not be decrypted.")
    return data


def redact(text: str) -> str:
    cleaned = text or ""
    for name in ("BALE_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "APP_SECRET_KEY", "ADMIN_PASSWORD"):
        secret = os.environ.get(name, "")
        if secret:
            cleaned = cleaned.replace(secret, "***")
    cleaned = re.sub(r"ya29\.[A-Za-z0-9_\-]+", "***", cleaned)
    cleaned = re.sub(r"bot[0-9]+:[A-Za-z0-9_\-]+", "bot***", cleaned)
    return cleaned[:500]
