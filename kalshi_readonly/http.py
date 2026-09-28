"""Signed Kalshi client. Redirects are refused so signed headers stay on Kalshi.

GET, POST, and DELETE all use the same RSA-PSS headers. The body is not part of
the signature. Other methods are refused.

429 and 503 retry with Retry-After when Kalshi sends one (capped), otherwise a
short exponential backoff with jitter. The retry budget is small so a scan
does not spend the shared IP quota. After the budget is spent the error is
rate_limited.
"""

from __future__ import annotations

import email.utils
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timezone

from kalshi_readonly.auth import USER_AGENT, api_base, signed_headers

_TIMEOUT = 20
_ALLOWED = frozenset({"GET", "POST", "DELETE"})
_RETRY_STATUSES = frozenset({429, 503})
_MAX_ATTEMPTS = 3
_BASE_DELAY = 0.25
_MAX_DELAY = 3.0


class RateLimitedError(RuntimeError):
    """Raised when a 429 or 503 has used the retry budget."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RuntimeError(f"refusing redirect ({code})")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req: urllib.request.Request, timeout: float = _TIMEOUT):
    return _OPENER.open(req, timeout=timeout)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


def _jitter() -> float:
    return random.uniform(0, _BASE_DELAY)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def signed_path(path: str) -> str:
    rel = path if path.startswith("/") else f"/{path}"
    return urllib.parse.urlparse(api_base() + rel.split("?", 1)[0]).path


def _url(path: str, query: dict | None) -> str:
    rel = path if path.startswith("/") else f"/{path}"
    base = api_base() + rel.split("?", 1)[0]
    if not query:
        return base
    return f"{base}?{urllib.parse.urlencode(query)}"


def _detail(body: bytes) -> str:
    text = body.decode("utf-8", errors="replace")
    if "PRIVATE KEY" in text or "-----BEGIN" in text:
        return ""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(parsed, dict):
        return ""
    parts = [parsed.get("code"), parsed.get("message")]
    detail = ": ".join(part for part in parts if isinstance(part, str) and part)
    if "PRIVATE KEY" in detail or "-----BEGIN" in detail:
        return ""
    return detail[:300]


def _retry_after_seconds(header: str | None) -> float | None:
    if header is None:
        return None
    text = header.strip()
    if not text:
        return None
    try:
        value = float(text)
    except ValueError:
        value = None
    else:
        return max(0.0, value)
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError, IndexError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (parsed - _now()).total_seconds())


def _delay(attempt: int, retry_after: str | None) -> float:
    parsed = _retry_after_seconds(retry_after)
    if parsed is not None:
        return min(_MAX_DELAY, parsed)
    base = min(_MAX_DELAY, _BASE_DELAY * (2 ** (attempt - 1)))
    return min(_MAX_DELAY, base + _jitter())


def _retry_after_header(err: urllib.error.HTTPError) -> str | None:
    headers = getattr(err, "headers", None)
    if headers is None:
        return None
    value = headers.get("Retry-After")
    return value if isinstance(value, str) else None


def _message(method: str, path: str, code: int, detail: str, *, rate: bool) -> str:
    message = f"Kalshi {method} {path} failed: {code}"
    if detail:
        message = f"{message} {detail}"
    if rate:
        return f"rate_limited: {message}"
    return message


def _exchange(method: str, path: str, build: Callable[[], urllib.request.Request]) -> dict:
    verb = method.upper()
    if verb not in _ALLOWED:
        raise RuntimeError(f"refusing {verb}")
    last_code = 0
    last_detail = ""
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        req = build()
        try:
            with _open(req) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            body = err.read() if err.fp is not None else b""
            last_code = int(err.code)
            last_detail = _detail(body)
            if last_code in _RETRY_STATUSES and attempt < _MAX_ATTEMPTS:
                _sleep(_delay(attempt, _retry_after_header(err)))
                continue
            message = _message(verb, path, last_code, last_detail, rate=last_code in _RETRY_STATUSES)
            if last_code in _RETRY_STATUSES:
                raise RateLimitedError(message) from None
            raise RuntimeError(message) from None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            raise RuntimeError(f"Kalshi {verb} {path} returned non-JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError(f"Kalshi {verb} {path} returned non-JSON")
        return payload
    raise RateLimitedError(_message(verb, path, last_code, last_detail, rate=True))


def public_get(path: str, query: dict | None = None) -> dict:
    sign_path = signed_path(path)

    def build() -> urllib.request.Request:
        return urllib.request.Request(
            _url(path, query),
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            method="GET",
        )

    return _exchange("GET", sign_path, build)


def auth_get(path: str, query: dict | None = None) -> dict:
    sign_path = signed_path(path)

    def build() -> urllib.request.Request:
        return urllib.request.Request(
            _url(path, query),
            headers=signed_headers("GET", sign_path),
            method="GET",
        )

    return _exchange("GET", sign_path, build)


def auth_call(method: str, path: str, query: dict | None = None, body: dict | None = None) -> dict:
    """Signed POST or DELETE. Fails closed in signed_headers when auth is missing."""
    verb = method.upper()
    if verb not in {"POST", "DELETE"}:
        raise RuntimeError(f"refusing {verb}")
    if verb == "DELETE" and body is not None:
        raise RuntimeError("refusing DELETE body")
    sign_path = signed_path(path)

    def build() -> urllib.request.Request:
        headers = signed_headers(verb, sign_path)
        payload = None
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            headers = {**headers, "Content-Type": "application/json"}
        return urllib.request.Request(
            _url(path, query),
            data=payload,
            headers=headers,
            method=verb,
        )

    return _exchange(verb, sign_path, build)
