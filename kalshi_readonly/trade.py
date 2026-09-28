"""Single-order Kalshi tools. No batches, deposits, withdrawals, or strategies.

Place, amend, and decrease use the V2 event-order routes. Cancel is DELETE on
the same order path. Open orders are a signed GET and do not require confirm.
"""

from __future__ import annotations

import re
import urllib.parse
from decimal import Decimal

from kalshi_readonly.guard import require_mutation
from kalshi_readonly.http import auth_call, auth_get

_COUNT_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,2})?$")
_PRICE_RE = re.compile(r"^(?:0|[1-9]\d*)(?:\.\d{1,4})?$")
_TICKER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SIDES = frozenset({"bid", "ask"})
_TIF = frozenset({"fill_or_kill", "good_till_canceled", "immediate_or_cancel"})
_STP = frozenset({"taker_at_cross", "maker"})

_CONFIRM = {
    "type": "boolean",
    "description": "Required boolean true from the caller. The server never sets this.",
}


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


def _fixed_count(value: object, field: str) -> str:
    if isinstance(value, bool):
        raise RuntimeError(f"{field} must be a fixed-point contract string")
    if isinstance(value, int):
        if value <= 0:
            raise RuntimeError(f"{field} must be greater than 0")
        return str(value)
    if not isinstance(value, str) or _COUNT_RE.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be a fixed-point contract string")
    if Decimal(value) <= 0:
        raise RuntimeError(f"{field} must be greater than 0")
    return value


def _fixed_price(value: object) -> str:
    if not isinstance(value, str) or _PRICE_RE.fullmatch(value) is None:
        raise RuntimeError("price must be a fixed-point dollar string")
    amount = Decimal(value)
    if amount <= 0 or amount >= 1:
        raise RuntimeError("price must be greater than 0 and less than 1")
    return value


def _ticker(value: object, field: str) -> str:
    if not isinstance(value, str) or _TICKER_RE.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be a market ticker")
    return value


def _optional_ticker(args: dict, field: str) -> str | None:
    if field not in args or args[field] is None:
        return None
    return _ticker(args[field], field)


def _order_id(value: object) -> str:
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise RuntimeError("order_id must be an order id")
    return urllib.parse.quote(value, safe="-_")


def _choice(value: object, field: str, allowed: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        joined = ", ".join(sorted(allowed))
        raise RuntimeError(f"{field} must be one of: {joined}")
    return value


def _book_side(value: object) -> str:
    if isinstance(value, str) and value.strip().lower() in {"yes", "no", "buy", "sell"}:
        raise RuntimeError("side must be bid or ask (bid buys YES, ask sells YES)")
    return _choice(value, "side", _SIDES)


def _optional_bool(args: dict, field: str) -> bool | None:
    if field not in args or args[field] is None:
        return None
    if not isinstance(args[field], bool):
        raise RuntimeError(f"{field} must be a boolean")
    return args[field]


def _optional_int(args: dict, field: str, low: int, high: int) -> int | None:
    if field not in args or args[field] is None:
        return None
    value = args[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{field} must be an integer from {low} to {high}")
    if value < low or value > high:
        raise RuntimeError(f"{field} must be an integer from {low} to {high}")
    return value


def _optional_id(args: dict, field: str) -> str | None:
    if field not in args or args[field] is None:
        return None
    value = args[field]
    if not isinstance(value, str) or _ID_RE.fullmatch(value) is None:
        raise RuntimeError(f"{field} must be an id string")
    return value


def _optional_cursor(args: dict) -> str | None:
    if "cursor" not in args or args["cursor"] in (None, ""):
        return None
    value = args["cursor"]
    if not isinstance(value, str) or len(value) > 1024 or any(char in value for char in "\r\n"):
        raise RuntimeError("cursor must be a short string")
    if "PRIVATE KEY" in value or "-----BEGIN" in value:
        raise RuntimeError("cursor must be a short string")
    return value


def _order_path(order_id: str, suffix: str = "") -> str:
    return f"/portfolio/events/orders/{order_id}{suffix}"


def _subaccount_query(args: dict) -> dict[str, object]:
    query: dict[str, object] = {}
    subaccount = _optional_int(args, "subaccount", 0, 63)
    if subaccount is not None:
        query["subaccount"] = subaccount
    return query


def list_open_orders(args: dict | None = None) -> dict:
    """Read resting orders. Does not require confirm and does not mutate."""
    args = args or {}
    query: dict[str, object] = {"status": "resting", "limit": _limit(args, 100)}
    ticker = _optional_ticker(args, "ticker")
    if ticker is not None:
        query["ticker"] = ticker
    cursor = _optional_cursor(args)
    if cursor is not None:
        query["cursor"] = cursor
    query.update(_subaccount_query(args))
    data = auth_get("/portfolio/orders", query)
    if not isinstance(data.get("orders"), list):
        raise RuntimeError("unexpected orders payload")
    return data


def place_order(args: dict | None = None) -> dict:
    args = args or {}
    require_mutation(args)
    ticker = _ticker(args.get("ticker"), "ticker")
    side = _book_side(args.get("side"))
    count = _fixed_count(args.get("count"), "count")
    price = _fixed_price(args.get("price"))
    time_in_force = _choice(args.get("time_in_force"), "time_in_force", _TIF)
    prevention = _choice(args.get("self_trade_prevention_type"), "self_trade_prevention_type", _STP)
    expiration = _optional_int(args, "expiration_time", 1, 4_000_000_000)
    reduce_only = _optional_bool(args, "reduce_only")
    if expiration is not None and time_in_force != "good_till_canceled":
        raise RuntimeError("expiration_time requires time_in_force good_till_canceled")
    if reduce_only is True and time_in_force != "immediate_or_cancel":
        raise RuntimeError("reduce_only requires time_in_force immediate_or_cancel")
    body: dict[str, object] = {
        "ticker": ticker,
        "side": side,
        "count": count,
        "price": price,
        "time_in_force": time_in_force,
        "self_trade_prevention_type": prevention,
    }
    client_order_id = _optional_id(args, "client_order_id")
    if client_order_id is not None:
        body["client_order_id"] = client_order_id
    if expiration is not None:
        body["expiration_time"] = expiration
    for field in ("post_only", "cancel_order_on_pause", "reduce_only"):
        flag = _optional_bool(args, field)
        if flag is not None:
            body[field] = flag
    subaccount = _optional_int(args, "subaccount", 0, 63)
    if subaccount is not None:
        body["subaccount"] = subaccount
    order_group_id = _optional_id(args, "order_group_id")
    if order_group_id is not None:
        body["order_group_id"] = order_group_id
    exchange_index = _optional_int(args, "exchange_index", -1, 63)
    if exchange_index is not None:
        body["exchange_index"] = exchange_index
    return auth_call("POST", "/portfolio/events/orders", body=body)


def cancel_order(args: dict | None = None) -> dict:
    args = args or {}
    require_mutation(args)
    order_id = _order_id(args.get("order_id"))
    query = _subaccount_query(args)
    market_ticker = _optional_ticker(args, "market_ticker")
    if market_ticker is not None:
        query["market_ticker"] = market_ticker
    exchange_index = _optional_int(args, "exchange_index", -1, 63)
    if exchange_index is not None:
        query["exchange_index"] = exchange_index
    return auth_call("DELETE", _order_path(order_id), query or None)


def amend_order(args: dict | None = None) -> dict:
    args = args or {}
    require_mutation(args)
    order_id = _order_id(args.get("order_id"))
    body: dict[str, object] = {
        "ticker": _ticker(args.get("ticker"), "ticker"),
        "side": _book_side(args.get("side")),
        "price": _fixed_price(args.get("price")),
        "count": _fixed_count(args.get("count"), "count"),
    }
    client_order_id = _optional_id(args, "client_order_id")
    if client_order_id is not None:
        body["client_order_id"] = client_order_id
    updated = _optional_id(args, "updated_client_order_id")
    if updated is not None:
        body["updated_client_order_id"] = updated
    exchange_index = _optional_int(args, "exchange_index", -1, 63)
    if exchange_index is not None:
        body["exchange_index"] = exchange_index
    return auth_call("POST", _order_path(order_id, "/amend"), _subaccount_query(args) or None, body)


def decrease_order(args: dict | None = None) -> dict:
    args = args or {}
    require_mutation(args)
    order_id = _order_id(args.get("order_id"))
    has_by = "reduce_by" in args and args["reduce_by"] is not None
    has_to = "reduce_to" in args and args["reduce_to"] is not None
    if has_by == has_to:
        raise RuntimeError("pass exactly one of reduce_by or reduce_to")
    body: dict[str, object] = {}
    if has_by:
        body["reduce_by"] = _fixed_count(args.get("reduce_by"), "reduce_by")
    else:
        body["reduce_to"] = _fixed_count(args.get("reduce_to"), "reduce_to")
    market_ticker = _optional_ticker(args, "market_ticker")
    if market_ticker is not None:
        body["market_ticker"] = market_ticker
    exchange_index = _optional_int(args, "exchange_index", -1, 63)
    if exchange_index is not None:
        body["exchange_index"] = exchange_index
    return auth_call("POST", _order_path(order_id, "/decrease"), _subaccount_query(args) or None, body)


OPEN_ORDERS_TOOL = {
    "name": "list_open_orders",
    "description": (
        "Read resting Kalshi orders (GET /portfolio/orders?status=resting). "
        "Auth required. Does not place, cancel, amend, or decrease. "
        "Stays available while KALSHI_SAFE_MODE is on. Optional ticker, limit (default 100), cursor, subaccount."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "ticker": {"type": "string"},
            "limit": {"type": "integer"},
            "cursor": {"type": "string"},
            "subaccount": {"type": "integer"},
        },
    },
}

_MUTATION_NOTE = (
    "Requires KALSHI_SAFE_MODE=0 and confirm=true (boolean). "
    "The server never sets confirm. There is no dollar max. No withdraw or deposit."
)

MUTATING_TOOLS = [
    {
        "name": "place_order",
        "description": (
            "Place one Kalshi order (POST /portfolio/events/orders). "
            "side is the YES book: bid buys YES, ask sells YES. "
            "price is a YES-side dollar string strictly between 0 and 1. "
            "count is contracts (fixed-point string or integer). "
            "time_in_force is fill_or_kill, good_till_canceled, or immediate_or_cancel. "
            "self_trade_prevention_type is taker_at_cross or maker. "
            "Pass client_order_id yourself if you need dedup. "
            + _MUTATION_NOTE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "side": {"type": "string", "enum": ["bid", "ask"]},
                "count": {"type": "string"},
                "price": {"type": "string"},
                "time_in_force": {
                    "type": "string",
                    "enum": ["fill_or_kill", "good_till_canceled", "immediate_or_cancel"],
                },
                "self_trade_prevention_type": {"type": "string", "enum": ["taker_at_cross", "maker"]},
                "client_order_id": {"type": "string"},
                "expiration_time": {"type": "integer"},
                "post_only": {"type": "boolean"},
                "reduce_only": {"type": "boolean"},
                "cancel_order_on_pause": {"type": "boolean"},
                "subaccount": {"type": "integer"},
                "order_group_id": {"type": "string"},
                "exchange_index": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": [
                "ticker",
                "side",
                "count",
                "price",
                "time_in_force",
                "self_trade_prevention_type",
                "confirm",
            ],
        },
    },
    {
        "name": "cancel_order",
        "description": (
            "Cancel one order (DELETE /portfolio/events/orders/{order_id}). "
            "Pass market_ticker so Kalshi can auto-route the cancel. "
            + _MUTATION_NOTE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "market_ticker": {"type": "string"},
                "subaccount": {"type": "integer"},
                "exchange_index": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "confirm"],
        },
    },
    {
        "name": "amend_order",
        "description": (
            "Amend one order price and total fillable count "
            "(POST /portfolio/events/orders/{order_id}/amend). "
            "count is already-filled plus the desired resting remainder, not a reduce-by amount. "
            "Decreasing size keeps queue position. A price change or a larger size does not. "
            "side is bid or ask on the YES book. "
            + _MUTATION_NOTE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "ticker": {"type": "string"},
                "side": {"type": "string", "enum": ["bid", "ask"]},
                "price": {"type": "string"},
                "count": {"type": "string"},
                "client_order_id": {"type": "string"},
                "updated_client_order_id": {"type": "string"},
                "exchange_index": {"type": "integer"},
                "subaccount": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "ticker", "side", "price", "count", "confirm"],
        },
    },
    {
        "name": "decrease_order",
        "description": (
            "Reduce one resting order (POST /portfolio/events/orders/{order_id}/decrease). "
            "Pass exactly one of reduce_by or reduce_to as a contract count. "
            + _MUTATION_NOTE
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "reduce_by": {"type": "string"},
                "reduce_to": {"type": "string"},
                "market_ticker": {"type": "string"},
                "exchange_index": {"type": "integer"},
                "subaccount": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "confirm"],
        },
    },
]

MUTATING_HANDLERS = {
    "place_order": place_order,
    "cancel_order": cancel_order,
    "amend_order": amend_order,
    "decrease_order": decrease_order,
}
