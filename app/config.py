import os
from pathlib import Path


def data_dir() -> Path:
    path = Path(os.environ.get("DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    return "sqlite:///" + (data_dir() / "app.db").resolve().as_posix()


def app_secret() -> str:
    return os.environ.get("APP_SECRET_KEY", "")


def app_base_url() -> str:
    return os.environ.get("APP_BASE_URL", "http://localhost:9050").rstrip("/")


def bale_base() -> str:
    return os.environ.get("BALE_API_BASE", "https://tapi.bale.ai").rstrip("/")


def telegram_base() -> str:
    return os.environ.get("TELEGRAM_API_BASE", "https://api.telegram.org").rstrip("/")


def bale_token() -> str:
    return os.environ.get("BALE_BOT_TOKEN", "").strip()


def telegram_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def telegram_proxy() -> str:
    return os.environ.get("TELEGRAM_PROXY_URL", "").strip()


def cookie_secure() -> bool:
    return app_base_url().startswith("https://")
