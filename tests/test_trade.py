import base64
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from kalshi_readonly.auth import AUTH_ERROR
from kalshi_readonly.caps import reset_ledger
from kalshi_readonly.guard import CONFIRM_ERROR, SAFE_MODE_ERROR
from kalshi_readonly.stdio import run_server
from kalshi_readonly.tools import HANDLERS, registered_tools
from kalshi_readonly.trade import (
    MUTATING_TOOLS,
    amend_order,
    cancel_order,
    decrease_order,
    list_open_orders,
    place_order,
)

@pytest.fixture(autouse=True)
def _clear_ledger():
    reset_ledger()
    yield
    reset_ledger()


_PLACE = {
    "ticker": "KXTEST",
    "side": "bid",
    "count": "1.00",
    "price": "0.4200",
    "time_in_force": "good_till_canceled",
    "self_trade_prevention_type": "taker_at_cross",
    "client_order_id": "ord-1",
    "confirm": True,
}


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


class _Body:
    def __init__(self, payload: dict):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


class _In:
    def __init__(self, data: bytes):
        self.buffer = io.BytesIO(data)


class _Out:
    def __init__(self):
        self.buffer = io.BytesIO()


def _auth(monkeypatch: pytest.MonkeyPatch) -> rsa.RSAPrivateKey:
    key, pem = _pem()
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-123")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_PEM", pem)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.delenv("KALSHI_API_BASE", raising=False)
    return key


def _enable_trading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")


def _frames(raw: bytes) -> list[dict]:
    view = io.BytesIO(raw)
    found: list[dict] = []
    while True:
        line = view.readline()
        if not line:
            break
        if not line.lower().startswith(b"content-length:"):
            continue
        length = int(line.split(b":", 1)[1].strip())
        assert view.readline() in (b"\r\n", b"\n")
        found.append(json.loads(view.read(length)))
    return found


def _verify(key: rsa.RSAPrivateKey, req: urllib.request.Request, method: str) -> None:
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
    assert _header(req, "KALSHI-ACCESS-KEY") == "key-123"


def test_confirm_schema_does_not_set_confirm() -> None:
    for tool in MUTATING_TOOLS:
        confirm = tool["inputSchema"]["properties"]["confirm"]
        assert "default" not in confirm
        assert "const" not in confirm
        assert "confirm" in tool["inputSchema"]["required"]


def test_catalog_has_no_transfer_or_strategy_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")
    names = [tool["name"] for tool in registered_tools()]
    blob = " ".join(names)
    assert "withdraw" not in blob
    assert "deposit" not in blob
    assert "transfer" not in blob
    for banned in ("max_profit", "auto_trade", "batch"):
        assert banned not in blob


@pytest.mark.parametrize("raw", [None, "", "1", "true", "YES", "on", "maybe"])
def test_safe_mode_defaults_on_and_hides_mutations(monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
    if raw is None:
        monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    else:
        monkeypatch.setenv("KALSHI_SAFE_MODE", raw)
    names = [tool["name"] for tool in registered_tools()]
    assert names == [
        "exchange_status",
        "list_markets",
        "find_best_bets",
        "cash_or_positions",
        "fe_routine",
        "list_open_orders",
    ]
    assert set(HANDLERS) >= {"place_order", "cancel_order", "amend_order", "decrease_order"}


@pytest.mark.parametrize("raw", ["0", "false", "OFF", "no", " 0 "])
def test_safe_mode_off_registers_trade_tools(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", raw)
    names = [tool["name"] for tool in registered_tools()]
    assert names[-4:] == ["place_order", "cancel_order", "amend_order", "decrease_order"]


@pytest.mark.parametrize(
    "confirm",
    [None, False, "true", "True", 1, "yes"],
)
def test_confirm_gate_refuses_without_http(monkeypatch: pytest.MonkeyPatch, confirm) -> None:
    _enable_trading(monkeypatch)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    args = dict(_PLACE)
    if confirm is None:
        args.pop("confirm")
    else:
        args["confirm"] = confirm
    with pytest.raises(RuntimeError) as caught:
        place_order(args)
    assert str(caught.value) == CONFIRM_ERROR


def test_safe_mode_refuses_even_when_confirm_is_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    _auth(monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    for call in (
        lambda: place_order(_PLACE),
        lambda: cancel_order({"order_id": "ord-1", "confirm": True}),
        lambda: amend_order({**_PLACE, "order_id": "ord-1"}),
        lambda: decrease_order({"order_id": "ord-1", "reduce_by": "1", "confirm": True}),
    ):
        with pytest.raises(RuntimeError) as caught:
            call()
        assert str(caught.value) == SAFE_MODE_ERROR


def test_missing_auth_fails_closed_before_place(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_trading(monkeypatch)
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PEM", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_PATH", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError) as caught:
        place_order(_PLACE)
    assert str(caught.value) == AUTH_ERROR
    assert "PRIVATE KEY" not in str(caught.value)


def _empty_book() -> _Body:
    return _Body({"market_positions": [], "event_positions": [], "fills": [], "orders": [], "cursor": ""})


def test_place_order_signs_post_and_omits_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _auth(monkeypatch)
    _enable_trading(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        assert timeout == 20
        seen.append(req)
        if req.get_method() == "GET":
            return _empty_book()
        return _Body({"order_id": "ord-1", "fill_count": "0.00", "remaining_count": "1.00", "ts_ms": 1})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = place_order(_PLACE)

    posts = [req for req in seen if req.get_method() == "POST"]
    assert len(posts) == 1
    assert any(req.get_method() == "GET" for req in seen)
    req = posts[0]
    assert req.get_method() == "POST"
    assert urllib.parse.urlparse(req.full_url).path == "/trade-api/v2/portfolio/events/orders"
    body = json.loads(req.data)
    assert body == {
        "ticker": "KXTEST",
        "side": "bid",
        "count": "1.00",
        "price": "0.4200",
        "time_in_force": "good_till_canceled",
        "self_trade_prevention_type": "taker_at_cross",
        "client_order_id": "ord-1",
    }
    assert "confirm" not in body
    assert _header(req, "Content-Type") == "application/json"
    _verify(key, req, "POST")
    blob = json.dumps(dict(req.header_items())) + req.data.decode("utf-8")
    assert "PRIVATE KEY" not in blob
    assert out["order_id"] == "ord-1"


def test_cancel_order_signs_delete_without_query(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _auth(monkeypatch)
    _enable_trading(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(req)
        return _Body({"order_id": "ord-1", "reduced_by": "1.00", "ts_ms": 2})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = cancel_order({"order_id": "ord-1", "market_ticker": "KXTEST", "confirm": True})

    req = seen[0]
    assert req.get_method() == "DELETE"
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/trade-api/v2/portfolio/events/orders/ord-1"
    assert urllib.parse.parse_qs(parsed.query)["market_ticker"] == ["KXTEST"]
    assert req.data is None
    assert _header(req, "Content-Type") is None
    _verify(key, req, "DELETE")
    assert out["reduced_by"] == "1.00"


def test_amend_and_decrease_hit_v2_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth(monkeypatch)
    _enable_trading(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(req)
        if req.get_method() == "GET":
            return _empty_book()
        if req.full_url.endswith("/amend"):
            return _Body({"order_id": "ord-1", "remaining_count": "1.00", "ts_ms": 3})
        return _Body({"order_id": "ord-1", "remaining_count": "0.50", "ts_ms": 4})

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    amend_order(
        {
            "order_id": "ord-1",
            "ticker": "KXTEST",
            "side": "ask",
            "price": "0.6100",
            "count": "2",
            "confirm": True,
        }
    )
    decrease_order({"order_id": "ord-1", "reduce_to": "0.50", "market_ticker": "KXTEST", "confirm": True})

    posts = [req for req in seen if req.get_method() == "POST"]
    assert [urllib.parse.urlparse(req.full_url).path for req in posts] == [
        "/trade-api/v2/portfolio/events/orders/ord-1/amend",
        "/trade-api/v2/portfolio/events/orders/ord-1/decrease",
    ]
    assert json.loads(posts[0].data)["count"] == "2"
    assert json.loads(posts[0].data)["side"] == "ask"
    assert "confirm" not in json.loads(posts[0].data)
    decrease = json.loads(posts[1].data)
    assert decrease == {"reduce_to": "0.50", "market_ticker": "KXTEST"}


def test_decrease_requires_exactly_one_amount(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_trading(monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError, match="exactly one"):
        decrease_order({"order_id": "ord-1", "reduce_by": "1", "reduce_to": "1", "confirm": True})
    with pytest.raises(RuntimeError, match="exactly one"):
        decrease_order({"order_id": "ord-1", "confirm": True})


def test_bad_order_inputs_do_not_call_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_trading(monkeypatch)
    _auth(monkeypatch)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    with pytest.raises(RuntimeError, match="bid or ask"):
        place_order({**_PLACE, "side": "yes"})
    with pytest.raises(RuntimeError, match="less than 1"):
        place_order({**_PLACE, "price": "1.00"})
    with pytest.raises(RuntimeError, match="order id"):
        cancel_order({"order_id": "ord/1", "confirm": True})


def test_place_error_does_not_return_key_material(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth(monkeypatch)
    _enable_trading(monkeypatch)

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        if req.get_method() == "GET":
            return _empty_book()
        body = b'-----BEGIN PRIVATE KEY-----\nSECRET\n-----END PRIVATE KEY-----'
        raise urllib.error.HTTPError(req.full_url, 401, "unauthorized", Message(), io.BytesIO(body))

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RuntimeError, match="POST") as caught:
        place_order(_PLACE)
    assert "failed: 401" in str(caught.value)
    assert "PRIVATE KEY" not in str(caught.value)
    assert "SECRET" not in str(caught.value)


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
                        "order_id": "ord-1",
                        "ticker": "KXTEST",
                        "book_side": "bid",
                        "status": "resting",
                        "remaining_count_fp": "1.00",
                    }
                ],
                "cursor": "",
            }
        )

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    out = list_open_orders({"limit": 5, "ticker": "KXTEST", "status": "executed"})
    req = seen[0]
    assert req.get_method() == "GET"
    parsed = urllib.parse.urlparse(req.full_url)
    assert parsed.path == "/trade-api/v2/portfolio/orders"
    query = urllib.parse.parse_qs(parsed.query)
    assert query["status"] == ["resting"]
    assert query["limit"] == ["5"]
    assert query["ticker"] == ["KXTEST"]
    _verify(key, req, "GET")
    assert out["orders"][0]["order_id"] == "ord-1"


def test_stdio_hides_trade_tools_and_refuses_a_direct_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)

    def fail_open(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.http._open", fail_open)
    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "place_order", "arguments": _PLACE},
        },
    ]
    raw = "".join(json.dumps(message) + "\n" for message in messages).encode()
    incoming = _In(raw)
    outgoing = _Out()
    monkeypatch.setattr(sys, "stdin", incoming)
    monkeypatch.setattr(sys, "stdout", outgoing)
    run_server("kalshi-readonly", "0.2.0", registered_tools, HANDLERS)
    frames = _frames(outgoing.buffer.getvalue())
    assert [frame["id"] for frame in frames] == [1, 2]
    names = [tool["name"] for tool in frames[0]["result"]["tools"]]
    assert "place_order" not in names
    assert "list_open_orders" in names
    assert frames[1]["result"]["isError"] is True
    assert SAFE_MODE_ERROR in frames[1]["result"]["content"][0]["text"]


def test_place_refuses_the_hard_max_before_sending_the_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _auth(monkeypatch)
    _enable_trading(monkeypatch)
    seen: list[urllib.request.Request] = []

    def fake_open(req: urllib.request.Request, timeout: float = 20):
        seen.append(req)
        if req.get_method() == "GET":
            return _empty_book()
        raise AssertionError("order was sent")

    monkeypatch.setattr("kalshi_readonly.http._open", fake_open)
    with pytest.raises(RuntimeError, match=r"hard max \$15"):
        place_order({**_PLACE, "count": "40", "price": "0.5000"})
    assert seen
    assert all(req.get_method() == "GET" for req in seen)
