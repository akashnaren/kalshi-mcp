"""Signed Kalshi client. Redirects are refused so signed headers stay on Kalshi."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from kalshi_readonly.auth import USER_AGENT, api_base, signed_headers

_TIMEOUT = 20


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        raise RuntimeError(f"refusing redirect ({code})")


_OPENER = urllib.request.build_opener(_NoRedirect)


def _open(req: urllib.request.Request, timeout: float = _TIMEOUT):
    return _OPENER.open(req, timeout=timeout)


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


_METHODS = frozenset({"GET", "POST", "DELETE"})


def _exchange(req: urllib.request.Request, path: str) -> dict:
    method = req.get_method()
    if method not in _METHODS:
        raise RuntimeError(f"refusing {method}")
    try:
        with _open(req) as response:
            raw = response.read()
            payload = {} if not raw else json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as err:
        detail = _detail(err.read() if err.fp is not None else b"")
        message = f"Kalshi {method} {path} failed: {err.code}"
        if detail:
            message = f"{message} {detail}"
        raise RuntimeError(message) from None
    except json.JSONDecodeError:
        raise RuntimeError(f"Kalshi {method} {path} returned non-JSON") from None
    if not isinstance(payload, dict):
        raise RuntimeError(f"Kalshi {method} {path} returned non-JSON")
    return payload


def public_get(path: str, query: dict | None = None) -> dict:
    sign_path = signed_path(path)
    req = urllib.request.Request(
        _url(path, query),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="GET",
    )
    return _exchange(req, sign_path)


def auth_get(path: str, query: dict | None = None) -> dict:
    sign_path = signed_path(path)
    req = urllib.request.Request(
        _url(path, query),
        headers=signed_headers("GET", sign_path),
        method="GET",
    )
    return _exchange(req, sign_path)


def auth_post(path: str, body: dict, query: dict | None = None) -> dict:
    sign_path = signed_path(path)
    headers = signed_headers("POST", sign_path)
    headers["Content-Type"] = "application/json"
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    req = urllib.request.Request(_url(path, query), data=data, headers=headers, method="POST")
    return _exchange(req, sign_path)


def auth_delete(path: str, query: dict | None = None) -> dict:
    sign_path = signed_path(path)
    req = urllib.request.Request(
        _url(path, query),
        headers=signed_headers("DELETE", sign_path),
        method="DELETE",
    )
    return _exchange(req, sign_path)
