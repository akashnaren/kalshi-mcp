import base64
import io
import json
import sys
import urllib.parse
import urllib.request

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_readonly.auth import AUTH_ERROR
from kalshi_readonly.gates import TRADE_WRITES, parse_safe_mode
from kalshi_readonly.stdio import run_server
from kalshi_readonly.tools import HANDLERS, visible_tools

_PLACE = {"ticker": "KXTEST", "side": "bid", "count": "1", "price": "0.0100", "client_order_id": "cid-1"}
_CANCEL = {"order_id": "order-1", "ticker": "KXTEST"}
_AMEND = {"order_id": "order-1", "ticker": "KXTEST", "side": "bid", "price": "0.0200", "count": "2"}
_DECREASE = {"order_id": "order-1", "reduce_by": "1", "ticker": "KXTEST"}


def _pem() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    return key, pem


def _header(req: urllib.request.Request, name: str) -> str | None:
    for key, value in req.header_items():
        if key.lower() == name.lower():
            return value
    return None


def _auth(monkeypatch: pytest.MonkeyPatch) -> rsa.RSAPrivateKey:
    key, pem = _pem()
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", pem)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("KALSHI_API_BASE", raising=False)
    return key


def _unlock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")


def _verify(req: urllib.request.Request, key: rsa.RSAPrivateKey, method: str) -> None:
    timestamp = _header(req, "KALSHI-ACCESS-TIMESTAMP")
    signature = _header(req, "KALSHI-ACCESS-SIGNATURE")
    assert timestamp and timestamp.isdigit()
    assert signature
    path = urllib.parse.urlparse(req.full_url).path
    key.public_key().verify(
        base64.b64decode(signature),
        f"{timestamp}{method}{path}".encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    assert "PRIVATE KEY" not in json.dumps(dict(req.header_items()))


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def _listed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    inbound = io.BytesIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}).encode() + b"\n")
    outbound = io.BytesIO()

    class _In:
        buffer = inbound

    class _Out:
        buffer = outbound

    monkeypatch.setattr(sys, "stdin", _In())
    monkeypatch.setattr(sys, "stdout", _Out())
    run_server("kalshi-readonly", "0.2.0", visible_tools, HANDLERS)
    raw = outbound.getvalue()
    _head, _, body = raw.partition(b"\r\n\r\n")
    listed = json.loads(body.decode())["result"]["tools"]
    return [tool["name"] for tool in listed]


def test_parse_safe_mode_defaults_on() -> None:
    assert parse_safe_mode(None) is True
    assert parse_safe_mode("  ") is True
    assert parse_safe_mode("1") is True
    assert parse_safe_mode("true") is True
    assert parse_safe_mode("yes") is True
    assert parse_safe_mode("2") is True
    assert parse_safe_mode("0") is False
    assert parse_safe_mode(" false ") is False
    assert parse_safe_mode("off") is False
    assert parse_safe_mode("no") is False


def test_safe_mode_hides_trade_writes_and_keeps_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    hidden = _listed(monkeypatch)
    assert hidden == ["exchange_status", "list_markets", "cash_or_positions", "list_open_orders"]
    assert TRADE_WRITES.isdisjoint(hidden)

    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")
    shown = _listed(monkeypatch)
    assert shown[:4] == hidden
    assert shown[4:] == ["place_order", "cancel_order", "amend_order", "decrease_order"]
    assert "withdraw" not in shown
    assert "deposit" not in shown


@pytest.mark.parametrize(
    ("name", "args"),
    [
        ("place_order", _PLACE),
        ("cancel_order", _CANCEL),
        ("amend_order", _AMEND),
        ("decrease_order", _DECREASE),
    ],
)
def test_confirm_gate_refuses_without_calling_http(name: str, args: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    _unlock(monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    for confirm in (None, False, "true", 1):
        call = dict(args)
        if confirm is not None:
            call["confirm"] = confirm
        with pytest.raises(RuntimeError, match="confirm-gated") as caught:
            HANDLERS[name](call)
        assert "Never set confirm yourself" in str(caught.value)


def test_safe_mode_refuses_confirmed_writes_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "1")

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    for name, args in (
        ("place_order", _PLACE),
        ("cancel_order", _CANCEL),
        ("amend_order", _AMEND),
        ("decrease_order", _DECREASE),
    ):
        call = dict(args)
        call["confirm"] = True
        with pytest.raises(RuntimeError, match="blocked by safe mode") as caught:
            HANDLERS[name](call)
        text = str(caught.value)
        assert "KALSHI_SAFE_MODE" in text
        assert "Never auto-confirm" in text


def test_missing_auth_fails_closed_before_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _unlock(monkeypatch)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError) as caught:
        HANDLERS["place_order"]({**_PLACE, "confirm": True})
    assert str(caught.value) == AUTH_ERROR
    with pytest.raises(RuntimeError) as listed:
        HANDLERS["list_open_orders"]({})
    assert str(listed.value) == AUTH_ERROR


def test_place_and_cancel_are_signed_and_drop_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _auth(monkeypatch)
    _unlock(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        assert timeout == 20
        seen.append(req)
        if req.get_method() == "POST":
            return _Body({"order_id": "order-1", "remaining_count": "1.00", "fill_count": "0.00", "ts_ms": 1})
        if req.get_method() == "DELETE":
            return _Body({"order_id": "order-1", "reduced_by": "1.00", "ts_ms": 2})
        raise AssertionError(req.get_method())

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    placed = HANDLERS["place_order"]({**_PLACE, "confirm": True})
    cancelled = HANDLERS["cancel_order"]({**_CANCEL, "confirm": True})

    assert placed["order_id"] == "order-1"
    assert cancelled["reduced_by"] == "1.00"
    assert [req.get_method() for req in seen] == ["POST", "DELETE"]

    post, delete = seen
    assert urllib.parse.urlparse(post.full_url).path == "/trade-api/v2/portfolio/events/orders"
    _verify(post, key, "POST")
    assert _header(post, "Content-Type") == "application/json"
    body = json.loads(post.data.decode())
    assert body["ticker"] == "KXTEST"
    assert body["side"] == "bid"
    assert body["count"] == "1"
    assert body["price"] == "0.0100"
    assert body["client_order_id"] == "cid-1"
    assert body["time_in_force"] == "good_till_canceled"
    assert body["self_trade_prevention_type"] == "taker_at_cross"
    assert "confirm" not in body
    assert "PRIVATE" not in post.data.decode()

    parsed = urllib.parse.urlparse(delete.full_url)
    assert parsed.path == "/trade-api/v2/portfolio/events/orders/order-1"
    assert urllib.parse.parse_qs(parsed.query)["market_ticker"] == ["KXTEST"]
    _verify(delete, key, "DELETE")
    assert delete.data is None


def test_amend_and_decrease_post_signed_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _auth(monkeypatch)
    _unlock(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(req)
        return _Body({"order_id": "order-1", "remaining_count": "1.00", "ts_ms": 3})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    HANDLERS["amend_order"]({**_AMEND, "confirm": True})
    HANDLERS["decrease_order"]({**_DECREASE, "confirm": True, "reduce_to": None})

    amend, decrease = seen
    assert urllib.parse.urlparse(amend.full_url).path == "/trade-api/v2/portfolio/events/orders/order-1/amend"
    assert json.loads(amend.data.decode()) == {
        "count": "2",
        "price": "0.0200",
        "side": "bid",
        "ticker": "KXTEST",
    }
    _verify(amend, key, "POST")
    assert urllib.parse.urlparse(decrease.full_url).path == "/trade-api/v2/portfolio/events/orders/order-1/decrease"
    assert json.loads(decrease.data.decode()) == {"market_ticker": "KXTEST", "reduce_by": "1"}
    _verify(decrease, key, "POST")


def test_decrease_requires_exactly_one_reducer(monkeypatch: pytest.MonkeyPatch) -> None:
    _unlock(monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError, match="exactly one"):
        HANDLERS["decrease_order"]({"order_id": "order-1", "confirm": True})
    with pytest.raises(RuntimeError, match="exactly one"):
        HANDLERS["decrease_order"]({"order_id": "order-1", "confirm": True, "reduce_by": "1", "reduce_to": "1"})


def test_list_open_orders_is_a_signed_get_in_safe_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _auth(monkeypatch)
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(req)
        return _Body(
            {
                "orders": [
                    {
                        "order_id": "order-1",
                        "ticker": "KXTEST",
                        "status": "resting",
                        "book_side": "bid",
                        "remaining_count_fp": "1.00",
                    }
                ],
                "cursor": "",
            }
        )

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = HANDLERS["list_open_orders"]({"limit": 5, "ticker": "KXTEST"})
    assert out["orders"][0]["order_id"] == "order-1"
    req = seen[0]
    assert req.get_method() == "GET"
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/trade-api/v2/portfolio/orders"
    query = urllib.parse.parse_qs(parsed.query)
    assert query["status"] == ["resting"]
    assert query["limit"] == ["5"]
    assert query["ticker"] == ["KXTEST"]
    _verify(req, key, "GET")
