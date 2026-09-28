"""Load the API key and sign requests. Never log or return the private key."""

from __future__ import annotations

import base64
import os
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_API_BASE = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "tinkabot-kalshi-mcp/0.1"
AUTH_ERROR = "auth required: set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH (or KALSHI_PRIVATE_KEY_PEM)"


def api_base() -> str:
    return (os.environ.get("KALSHI_API_BASE") or DEFAULT_API_BASE).strip().rstrip("/")


def load_auth() -> tuple[str, str]:
    key_id = (os.environ.get("KALSHI_API_KEY_ID") or "").strip()
    inline = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    pem_path = (os.environ.get("KALSHI_PRIVATE_KEY_PATH") or "").strip()
    pem = inline.strip() if inline and inline.strip() else ""
    if not pem and pem_path:
        try:
            pem = Path(pem_path).read_text(encoding="utf-8")
        except OSError:
            raise RuntimeError("unable to read KALSHI_PRIVATE_KEY_PATH") from None
    if not key_id or "PRIVATE KEY" not in pem:
        raise RuntimeError(AUTH_ERROR)
    return key_id, pem


def sign_request(pem: str, timestamp_ms: str, method: str, path: str) -> str:
    """RSA-PSS SHA-256 over `timestamp + METHOD + path`. Query strings are not signed."""
    message = f"{timestamp_ms}{method.upper()}{path.split('?', 1)[0]}".encode("utf-8")
    private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
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
