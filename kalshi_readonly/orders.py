"""Signed order tools. No deposit, withdraw, or strategy.

Writes call the V2 event-order routes. Listing open orders is a GET of resting
orders and stays available while safe mode is on.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal, InvalidOperation

from kalshi_readonly.gates import guard_trade
from kalshi_readonly.http import auth_delete, auth_get, auth_post

_TICKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ORDER_ID = re.compile(r"^[A-Za-z0-9-]{1,80}$")
_CLIENT_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_COUNT = re.compile(r"^(?:0|[1-9]\d{0,9})(?:\.\d{1,2})?$")
_PRICE = re.compile(r"^0\.\d{2,4}$")
_SIDES = frozenset({"bid", "ask"})
_TIF = frozenset({"fill_or_kill", "good_till_canceled", "immediate_or_cancel"})
_STP = frozenset({"taker_at_cross", "maker"})


def _reject(name: str) -> RuntimeError:
    return RuntimeError(f"{name} is invalid")


def _text(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str):
        raise _reject(name)
    text = value.strip()
    if "PRIVATE" in text or "-----BEGIN" in text or pattern.fullmatch(text) is None:
        raise _reject(name)
    return text


def _count(value: object, name: str, *, allow_zero: bool) -> str:
    if isinstance(value, bool):
        raise _reject(name)
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        raise _reject(name)
    if _COUNT.fullmatch(text) is None:
        raise _reject(name)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise _reject(name) from None
    if number < 0 or (number == 0 and not allow_zero):
        raise _reject(name)
    return text


def _price(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError('price must be a fixed-point dollar string such as "0.5600"')
    text = value.strip()
    if _PRICE.fullmatch(text) is None:
        raise RuntimeError('price must be a fixed-point dollar string such as "0.5600"')
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise RuntimeError('price must be a fixed-point dollar string such as "0.5600"') from None
    if number <= 0 or number >= 1:
        raise RuntimeError('price must be a fixed-point dollar string such as "0.5600"')
    return text


def _choice(value: object, name: str, allowed: frozenset[str], default: str | None = None) -> str:
    if value is None:
        if default is None:
            raise _reject(name)
        return default
    if not isinstance(value, str) or value.strip() not in allowed:
        raise _reject(name)
    return value.strip()


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise _reject(name)
    return value


def _optional_bool(args: dict, name: str, body: dict) -> None:
    if name not in args or args[name] is None:
        return
    body[name] = _bool(args[name], name)


def _int(value: object, name: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or value > high:
        raise _reject(name)
    return value


def _optional_int(args: dict, name: str, body: dict, low: int, high: int) -> None:
    if name not in args or args[name] is None:
        return
    body[name] = _int(args[name], name, low, high)


def _order_id(args: dict) -> str:
    return _text(args.get("order_id"), "order_id", _ORDER_ID)


def _ticker(value: object, name: str = "ticker") -> str:
    return _text(value, name, _TICKER)


def _client_id(value: object, name: str) -> str:
    return _text(value, name, _CLIENT_ID)


def _subaccount_query(args: dict) -> dict[str, object]:
    if "subaccount" not in args or args["subaccount"] is None:
        return {}
    return {"subaccount": _int(args["subaccount"], "subaccount", 0, 63)}


def _limit(args: dict, default: int) -> int:
    raw = args.get("limit", default)
    if isinstance(raw, bool):
        raise RuntimeError("limit must be an integer from 1 to 1000")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise RuntimeError("limit must be an integer from 1 to 1000") from None
    if value < 1 or value > 1000:
        raise RuntimeError("limit must be an integer from 1 to 1000")
    return value


def present_orders(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("unexpected orders payload")
    orders = raw.get("orders")
    if orders is None:
        orders = []
    if not isinstance(orders, list):
        raise RuntimeError("unexpected orders payload")
    return {
        "orders": [row for row in orders if isinstance(row, dict)],
        "cursor": raw.get("cursor") or "",
    }


def list_open_orders(args: dict | None = None) -> dict:
    """Resting orders. A read: no confirm, and safe mode does not hide it."""
    args = args or {}
    query: dict[str, object] = {"status": "resting", "limit": _limit(args, 50)}
    if args.get("ticker"):
        query["ticker"] = _ticker(args.get("ticker"))
    cursor = args.get("cursor")
    if cursor not in (None, ""):
        query["cursor"] = _text(cursor, "cursor", re.compile(r"^[A-Za-z0-9+/=._:-]{1,2048}$"))
    return present_orders(auth_get("/portfolio/orders", query))


def place_order(args: dict | None = None) -> dict:
    args = args or {}
    guard_trade("place_order", args.get("confirm"))
    time_in_force = _choice(args.get("time_in_force"), "time_in_force", _TIF, "good_till_canceled")
    body: dict[str, object] = {
        "ticker": _ticker(args.get("ticker")),
        "side": _choice(args.get("side"), "side", _SIDES),
        "count": _count(args.get("count"), "count", allow_zero=False),
        "price": _price(args.get("price")),
        "time_in_force": time_in_force,
        "self_trade_prevention_type": _choice(
            args.get("self_trade_prevention_type"),
            "self_trade_prevention_type",
            _STP,
            "taker_at_cross",
        ),
    }
    client_order_id = args.get("client_order_id")
    body["client_order_id"] = (
        str(uuid.uuid4()) if client_order_id in (None, "") else _client_id(client_order_id, "client_order_id")
    )
    if "expiration_time" in args and args["expiration_time"] is not None:
        if time_in_force != "good_till_canceled":
            raise RuntimeError("expiration_time requires time_in_force good_till_canceled")
        body["expiration_time"] = _int(args["expiration_time"], "expiration_time", 1, 4_000_000_000)
    _optional_bool(args, "post_only", body)
    _optional_bool(args, "cancel_order_on_pause", body)
    if "reduce_only" in args and args["reduce_only"] is not None:
        reduce_only = _bool(args["reduce_only"], "reduce_only")
        if reduce_only and time_in_force != "immediate_or_cancel":
            raise RuntimeError("reduce_only requires time_in_force immediate_or_cancel")
        body["reduce_only"] = reduce_only
    _optional_int(args, "subaccount", body, 0, 63)
    _optional_int(args, "exchange_index", body, -1, 32)
    if args.get("order_group_id"):
        body["order_group_id"] = _client_id(args.get("order_group_id"), "order_group_id")
    return auth_post("/portfolio/events/orders", body)


def cancel_order(args: dict | None = None) -> dict:
    args = args or {}
    guard_trade("cancel_order", args.get("confirm"))
    order_id = _order_id(args)
    query = _subaccount_query(args)
    query["market_ticker"] = _ticker(args.get("ticker"))
    if "exchange_index" in args and args["exchange_index"] is not None:
        query["exchange_index"] = _int(args["exchange_index"], "exchange_index", -1, 32)
    return auth_delete(f"/portfolio/events/orders/{order_id}", query)


def amend_order(args: dict | None = None) -> dict:
    args = args or {}
    guard_trade("amend_order", args.get("confirm"))
    order_id = _order_id(args)
    body: dict[str, object] = {
        "ticker": _ticker(args.get("ticker")),
        "side": _choice(args.get("side"), "side", _SIDES),
        "price": _price(args.get("price")),
        "count": _count(args.get("count"), "count", allow_zero=False),
    }
    if args.get("client_order_id"):
        body["client_order_id"] = _client_id(args.get("client_order_id"), "client_order_id")
    if args.get("updated_client_order_id"):
        body["updated_client_order_id"] = _client_id(args.get("updated_client_order_id"), "updated_client_order_id")
    _optional_int(args, "exchange_index", body, -1, 32)
    return auth_post(f"/portfolio/events/orders/{order_id}/amend", body, _subaccount_query(args) or None)


def decrease_order(args: dict | None = None) -> dict:
    args = args or {}
    guard_trade("decrease_order", args.get("confirm"))
    order_id = _order_id(args)
    has_by = "reduce_by" in args and args["reduce_by"] is not None
    has_to = "reduce_to" in args and args["reduce_to"] is not None
    if has_by == has_to:
        raise RuntimeError("decrease_order needs exactly one of reduce_by or reduce_to")
    body: dict[str, object] = {}
    if has_by:
        body["reduce_by"] = _count(args.get("reduce_by"), "reduce_by", allow_zero=False)
    else:
        body["reduce_to"] = _count(args.get("reduce_to"), "reduce_to", allow_zero=True)
    if args.get("ticker"):
        body["market_ticker"] = _ticker(args.get("ticker"))
    _optional_int(args, "exchange_index", body, -1, 32)
    return auth_post(f"/portfolio/events/orders/{order_id}/decrease", body, _subaccount_query(args) or None)
