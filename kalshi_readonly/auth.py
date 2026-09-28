"""Load the API key and sign requests. Never log or return the private key."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "tinkabot-kalshi-mcp/0.3"
AUTH_ERROR = (
    "auth required: set KALSHI_API_KEY_ID or KALSHI_API_KEY_ID_PATH "
    "and KALSHI_PRIVATE_KEY_PATH (or KALSHI_PRIVATE_KEY_PEM). "
    "Defaults: ~/.secrets/kalshi/key_id and ~/.secrets/kalshi/private.pem"
)


def api_base() -> str:
    return (os.environ.get("KALSHI_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/")


def _default_secret(name: str) -> Path:
    return Path.home() / ".secrets" / "kalshi" / name


_AUTH: tuple[str, str, str] | None = None
_KEYS: dict[str, object] = {}


def _clean_key_id(text: str) -> str:
    key_id = text.strip()
    if not key_id or len(key_id) > 256 or "PRIVATE KEY" in key_id or "-----BEGIN" in key_id:
        return ""
    return key_id


def _read_key_file(path: Path) -> str:
    try:
        return _clean_key_id(path.read_text(encoding="utf-8"))
    except OSError:
        raise RuntimeError("unable to read KALSHI_API_KEY_ID_PATH") from None


def _load_key_id() -> str:
    direct = _clean_key_id(os.environ.get("KALSHI_API_KEY_ID") or "")
    if direct:
        return direct
    path = (os.environ.get("KALSHI_API_KEY_ID_PATH") or "").strip()
    if path:
        return _read_key_file(Path(path))
    default = _default_secret("key_id")
    if not default.is_file():
        return ""
    try:
        return _clean_key_id(default.read_text(encoding="utf-8"))
    except OSError:
        return ""


def _pem_source() -> tuple[str, str]:
    inline = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    inline_pem = inline.strip() if inline and inline.strip() else ""
    pem_path = (os.environ.get("KALSHI_PRIVATE_KEY_PATH") or "").strip()
    if not pem_path and not inline_pem:
        default = _default_secret("private.pem")
        if default.is_file():
            pem_path = str(default)
    return inline_pem, pem_path


def load_auth() -> tuple[str, str]:
    """Return the key id and PEM. Reuse them until the env or key file changes."""
    global _AUTH
    key_id = _load_key_id()
    inline_pem, pem_path = _pem_source()
    token = ""
    if inline_pem:
        token = "inline:" + hashlib.sha256(inline_pem.encode("utf-8")).hexdigest()
    elif pem_path:
        try:
            stat = Path(pem_path).stat()
        except OSError:
            raise RuntimeError("unable to read KALSHI_PRIVATE_KEY_PATH") from None
        token = f"file:{pem_path}:{stat.st_mtime_ns}:{stat.st_size}"
    cached = _AUTH
    if cached is not None and cached[0] == key_id and cached[1] == token and key_id and token:
        return key_id, cached[2]
    pem = inline_pem
    if not pem and pem_path:
        try:
            pem = Path(pem_path).read_text(encoding="utf-8")
        except OSError:
            raise RuntimeError("unable to read KALSHI_PRIVATE_KEY_PATH") from None
    if not key_id or "PRIVATE KEY" not in pem:
        raise RuntimeError(AUTH_ERROR)
    _AUTH = (key_id, token, pem)
    return key_id, pem


def _private_key(pem: str):
    digest = hashlib.sha256(pem.encode("utf-8")).hexdigest()
    found = _KEYS.get(digest)
    if found is not None:
        return found
    key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    if len(_KEYS) >= 4:
        _KEYS.clear()
    _KEYS[digest] = key
    return key


def sign_request(pem: str, timestamp_ms: str, method: str, path: str) -> str:
    """RSA-PSS SHA-256 over `timestamp + METHOD + path`. Query strings are not signed."""
    message = f"{timestamp_ms}{method.upper()}{path.split('?', 1)[0]}".encode("utf-8")
    private_key = _private_key(pem)
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("ascii")


def signed_headers(method: str, path: str, now_ms: int | None = None) -> dict[str, str]:
    key_id, pem = load_auth()
    timestamp = str(int(time.time() * 1000) if now_ms is None else now_ms)
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "KALSHI-ACCESS-SIGNATURE": sign_request(pem, timestamp, method, path),
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
