"""Install requirements from the first package index that actually lists fastapi."""

import subprocess
import sys
import urllib.request
from urllib.parse import urlparse

INDEXES = (
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://pypi.org/simple",
)


def lists_fastapi(index: str) -> bool:
    try:
        with urllib.request.urlopen(index + "/fastapi/", timeout=8) as response:
            chunk = response.read(12000)
    except Exception:
        return False
    return b"fastapi-" in chunk


def main() -> int:
    chosen = next((index for index in INDEXES if lists_fastapi(index)), None)
    if chosen is None:
        print("No package index returned fastapi", file=sys.stderr)
        return 1
    host = urlparse(chosen).hostname or ""
    print(f"Installing Python packages from {chosen}", flush=True)
    return subprocess.call([
        "pip", "install", "--no-cache-dir",
        "-i", chosen, "--trusted-host", host,
        "-r", "requirements.txt",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
