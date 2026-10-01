"""429 and 503 retry with a hard attempt budget. Other status codes do not."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.message import Message
from email.utils import formatdate

import pytest

from kalshi_readonly.http import RateLimitedError, _DETAIL_CAP, _MAX_ATTEMPTS, _MAX_DELAY, _detail, public_get


class _Body:
    def __init__(self, payload: dict):
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> bool:
        return False


def _headers(**fields: str) -> Message:
    message = Message()
    for key, value in fields.items():
        message[key] = value
    return message


def _http_error(req: urllib.request.Request, code: int, body: bytes = b"", **headers: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(req.full_url, code, "error", _headers(**headers), io.BytesIO(body))


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    waited: list[float] = []
    monkeypatch.setattr("kalshi_readonly.http._sleep", lambda seconds: waited.append(seconds))
    monkeypatch.setattr("kalshi_readonly.http._jitter", lambda: 0.0)
    return waited


def test_429_honors_retry_after_then_returns_json(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(req, 429, b'{"message":"slow down"}', **{"Retry-After": "1.5"})
        return _Body({"markets": []})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    assert public_get("/markets", {"limit": 1}) == {"markets": []}
    assert calls["n"] == 2
    assert sleeps == [1.5]


def test_429_without_retry_after_uses_exponential_backoff_then_rate_limited(
    monkeypatch: pytest.MonkeyPatch, sleeps: list[float]
) -> None:
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        raise _http_error(req, 429, b'{"code":"too_many","message":"slow down"}')

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RateLimitedError, match=r"^rate_limited: Kalshi GET .+ failed: 429 too_many: slow down$") as caught:
        public_get("/markets")
    assert calls["n"] == _MAX_ATTEMPTS
    assert len(sleeps) == _MAX_ATTEMPTS - 1
    assert sleeps == [0.25, 0.5]
    assert "PRIVATE KEY" not in str(caught.value)


def test_503_retries_and_retry_after_is_capped(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        if calls["n"] < _MAX_ATTEMPTS:
            raise _http_error(req, 503, **{"Retry-After": "30"})
        return _Body({"exchange_active": True})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    assert public_get("/exchange/status") == {"exchange_active": True}
    assert sleeps == [_MAX_DELAY, _MAX_DELAY]


def test_retry_after_http_date_is_honored(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    fixed = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("kalshi_readonly.http._now", lambda: fixed)
    header = formatdate((fixed + timedelta(seconds=2)).timestamp(), usegmt=True)
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(req, 429, **{"Retry-After": header})
        return _Body({"ok": True})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    assert public_get("/exchange/status") == {"ok": True}
    assert sleeps == [pytest.approx(2.0)]


def test_garbage_retry_after_falls_back_to_backoff(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _http_error(req, 429, **{"Retry-After": "soon"})
        return _Body({"ok": True})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    assert public_get("/markets") == {"ok": True}
    assert sleeps == [0.25]


_WA_CODE = (
    "Trading_is_not_currently_allowed_in_Washington_on_markets_in_Sports,"
    "_Elections,_Politics,_Culture,_Tech_and_Science,_and_Mentions."
)
_WA_MESSAGE = (
    "Trading is not currently allowed in Washington on markets in Sports, "
    "Elections, Politics, Culture, Tech and Science, and Mentions."
)


def test_detail_nested_error_includes_code_message_and_details() -> None:
    body = json.dumps(
        {"error": {"code": "bad_request", "message": "nope", "details": "ticker required"}}
    ).encode()
    assert _detail(body) == "bad_request: nope: ticker required"


def test_detail_nested_washington_sports_code_and_message_fit() -> None:
    body = json.dumps({"error": {"code": _WA_CODE, "message": _WA_MESSAGE}}).encode()
    detail = _detail(body)
    assert detail == f"{_WA_CODE}: {_WA_MESSAGE}"
    assert len(detail) <= _DETAIL_CAP


def test_detail_top_level_code_message_and_details() -> None:
    body = b'{"code":"too_many","message":"slow down","details":"retry later"}'
    assert _detail(body) == "too_many: slow down: retry later"


def test_detail_empty_and_non_object_bodies() -> None:
    assert _detail(b"") == ""
    assert _detail(b"{}") == ""
    assert _detail(b"not-json") == ""
    assert _detail(b"[]") == ""
    assert _detail(b"null") == ""


def test_detail_drops_private_key_material() -> None:
    nested = json.dumps(
        {"error": {"code": "auth", "message": "-----BEGIN PRIVATE KEY-----\nSECRET"}}
    ).encode()
    assert _detail(nested) == ""
    leaked = b'{"code":"ok","message":"see PRIVATE KEY"}'
    assert _detail(leaked) == ""
    encoded = b'{"message":"\\u0050RIVATE KEY"}'
    assert "PRIVATE KEY" not in encoded.decode()
    assert _detail(encoded) == ""


def test_detail_caps_long_text() -> None:
    body = json.dumps({"code": "x" * 400, "message": "y" * 50}).encode()
    assert len(_detail(body)) == _DETAIL_CAP


def test_403_nested_error_surfaces_code_and_message(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    body = json.dumps({"error": {"code": _WA_CODE, "message": _WA_MESSAGE}}).encode()

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        raise _http_error(req, 403, body)

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RuntimeError, match="failed: 403") as caught:
        public_get("/portfolio/events/orders")
    text = str(caught.value)
    assert _WA_CODE in text
    assert _WA_MESSAGE in text
    assert "rate_limited" not in text
    assert sleeps == []


def test_400_does_not_retry(monkeypatch: pytest.MonkeyPatch, sleeps: list[float]) -> None:
    calls = {"n": 0}

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        calls["n"] += 1
        raise _http_error(req, 400, b'{"message":"bad"}')

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RuntimeError, match="failed: 400") as caught:
        public_get("/markets")
    assert not isinstance(caught.value, RateLimitedError)
    assert "rate_limited" not in str(caught.value)
    assert calls["n"] == 1
    assert sleeps == []
