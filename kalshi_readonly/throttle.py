"""Cross-process spacing for GET /markets.

Several stdio hosts can share one API key and one IP. A lock file under
KALSHI_STATE_DIR (or /tmp/kalshi-mcp when that variable is unset) keeps those
GETs at least 1.5 seconds apart. flock is released when the process exits.
"""

from __future__ import annotations

import fcntl
import os
import time
from pathlib import Path

_MARKETS_INTERVAL = 1.5
_LOCK_NAME = "markets-get.lock"
_FALLBACK_DIR = Path("/tmp/kalshi-mcp")


def throttle_dir() -> Path:
    raw = os.environ.get("KALSHI_STATE_DIR")
    if raw is not None and str(raw).strip():
        return Path(str(raw).strip()).expanduser()
    return _FALLBACK_DIR


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _now() -> float:
    return time.time()


def _parse_last(raw: bytes) -> float:
    text = raw.decode("utf-8", errors="replace").strip()
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def wait_markets_slot() -> None:
    """Block so this GET /markets starts at least 1.5s after the previous one."""
    directory = throttle_dir()
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(directory / _LOCK_NAME, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            last = _parse_last(os.read(fd, 128))
            now = _now()
            delay = _MARKETS_INTERVAL - (now - last)
            if delay > _MARKETS_INTERVAL:
                delay = _MARKETS_INTERVAL
            if delay > 0:
                _sleep(delay)
                now = _now()
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            os.write(fd, f"{now:.6f}\n".encode())
            os.fsync(fd)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
