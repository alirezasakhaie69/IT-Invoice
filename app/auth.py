import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import func, select

from app.config import app_secret
from app.models import LoginAttempt, SessionRow, User

SESSION_TTL = timedelta(hours=3)
LOCKOUT_WINDOW = timedelta(minutes=15)
LOCKOUT_LIMIT = 5
_HASHER = PasswordHasher()
_DUMMY_HASH: str | None = None


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError):
        return False


def _dummy_verify(password: str) -> None:
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("not-a-real-password")
    verify_password(password, _DUMMY_HASH)


def session_expiry(login_at: datetime) -> datetime:
    return login_at + SESSION_TTL


def create_session(db, user: User, now: datetime | None = None) -> SessionRow:
    moment = now or utcnow()
    row = SessionRow(
        id=secrets.token_urlsafe(32),
        user_id=user.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=moment,
        expires_at=session_expiry(moment),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session(db, token: str, now: datetime | None = None) -> SessionRow | None:
    """Return a live session. A request does not move expires_at."""
    if not token:
        return None
    moment = now or utcnow()
    row = db.get(SessionRow, token)
    if row is None:
        return None
    if moment >= row.expires_at:
        db.delete(row)
        db.commit()
        return None
    return row


def delete_session(db, token: str) -> None:
    row = db.get(SessionRow, token)
    if row is not None:
        db.delete(row)
        db.commit()


def failure_count(db, ip: str, username: str, now: datetime) -> int:
    cutoff = now - LOCKOUT_WINDOW
    return db.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.ip == ip,
            LoginAttempt.username == username,
            LoginAttempt.created_at >= cutoff,
        )
    ) or 0


def too_many_failures(db, ip: str, username: str, now: datetime) -> bool:
    return failure_count(db, ip, username, now) >= LOCKOUT_LIMIT


def record_failure(db, ip: str, username: str, now: datetime) -> None:
    db.add(LoginAttempt(ip=ip[:64], username=username[:80], created_at=now))
    db.commit()


def authenticate(db, username: str, password: str, ip: str, now: datetime) -> tuple[User | None, str | None]:
    if too_many_failures(db, ip, username, now):
        return None, "rate_limited"
    user = db.scalar(select(User).where(User.username == username))
    if user is None:
        _dummy_verify(password)
        record_failure(db, ip, username, now)
        return None, "invalid_login"
    if not verify_password(password, user.password_hash):
        record_failure(db, ip, username, now)
        if too_many_failures(db, ip, username, now):
            return None, "rate_limited"
        return None, "invalid_login"
    return user, None


def make_login_csrf() -> str:
    token = secrets.token_urlsafe(24)
    mac = hmac.new(app_secret().encode(), token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{mac}"


def login_csrf_ok(cookie: str, form_token: str) -> bool:
    if not cookie or not form_token or len(cookie) != len(form_token):
        return False
    if not hmac.compare_digest(cookie, form_token):
        return False
    token, separator, mac = cookie.partition(".")
    if not token or not separator or not mac:
        return False
    expected = hmac.new(app_secret().encode(), token.encode(), hashlib.sha256).hexdigest()
    if len(expected) != len(mac):
        return False
    return hmac.compare_digest(expected, mac)


def tokens_match(expected: str, presented: str) -> bool:
    if not expected or not presented or len(expected) != len(presented):
        return False
    return hmac.compare_digest(expected, presented)


def admin_count(db) -> int:
    return db.scalar(select(func.count()).select_from(User).where(User.role == "admin")) or 0


def seed_admin(db) -> None:
    if db.scalar(select(func.count()).select_from(User)):
        return
    username = os_username()
    password = os_password()
    if not username or not password:
        raise SystemExit("ADMIN_USERNAME and ADMIN_PASSWORD must be set before the first boot.")
    db.add(User(
        username=username,
        password_hash=hash_password(password),
        role="admin",
        locale="fa",
        created_at=utcnow(),
    ))
    db.commit()


def os_username() -> str:
    import os
    return os.environ.get("ADMIN_USERNAME", "").strip()


def os_password() -> str:
    import os
    return os.environ.get("ADMIN_PASSWORD", "")
