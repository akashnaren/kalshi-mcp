"""Single-order Kalshi tools. No batches, deposits, withdrawals, or strategies.

Place, amend, and decrease use the V2 event-order routes. Cancel is DELETE on
the same order path. Open orders are a signed GET and do not require confirm.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from kalshi_readonly.caps import enforce_open, load_caps, note_open, opening_notional, summarize_book
from kalshi_readonly.guard import require_mutation
from kalshi_readonly.http import auth_call, auth_get
from kalshi_readonly.ledger import MIRROR_LEDGER, record_fill
from kalshi_readonly.score import fee_dome_cents, flb_band, maker_fee_cents, series_fees

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


def _optional_group(args: dict) -> str | None:
    if "corr_group" not in args or args["corr_group"] in (None, ""):
        return None
    value = args["corr_group"]
    if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,48}", value):
        raise RuntimeError("corr_group must be a snake_case risk driver")
    return value


def _load_book() -> dict:
    positions = auth_get("/portfolio/positions", {"limit": 200})
    fills = auth_get("/portfolio/fills", {"limit": 200})
    orders = auth_get("/portfolio/orders", {"status": "resting", "limit": 200})
    return summarize_book(positions, fills, orders)


def _guard_open(
    *,
    ticker: str,
    side: str,
    count: str,
    price: str,
    corr_group: str | None,
    reduce_only: bool | None,
) -> tuple[dict | None, Decimal]:
    if reduce_only is True:
        return None, Decimal(0)
    notional = opening_notional(side, Decimal(count), Decimal(price))
    entry = Decimal(price) if side == "bid" else Decimal(1) - Decimal(price)
    try:
        book = _load_book()
    except RuntimeError as exc:
        if str(exc).startswith("auth required"):
            raise
        raise RuntimeError(f"refusing to add risk because the book could not be loaded: {exc}") from None
    enforce_open(notional=notional, price=entry, ticker=ticker, book=book, corr_group=corr_group)
    return book, notional


def _optional_edge(args: dict) -> str | None:
    if "edge_net_cents" not in args or args["edge_net_cents"] is None:
        return None
    value = args["edge_net_cents"]
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise RuntimeError("edge_net_cents must be a number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuntimeError("edge_net_cents must be a number") from None
    if amount < Decimal("-100") or amount > Decimal("100"):
        raise RuntimeError("edge_net_cents must be between -100 and 100")
    return format(amount, "f")


def _decision_id(args: dict) -> str | None:
    if "decision_id" not in args or args["decision_id"] in (None, ""):
        return None
    value = args["decision_id"]
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,180}", value):
        raise RuntimeError("decision_id must be a decision id")
    return value


def _response_order_id(result: dict) -> str | None:
    sources = [result]
    order = result.get("order")
    if isinstance(order, dict):
        sources.append(order)
    for source in sources:
        for key in ("order_id", "id"):
            value = source.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _response_fill_count(result: dict) -> Decimal:
    sources = [result]
    order = result.get("order")
    if isinstance(order, dict):
        sources.append(order)
    for source in sources:
        for key in ("fill_count_fp", "fill_count"):
            raw = source.get(key)
            if raw in (None, ""):
                continue
            try:
                return Decimal(str(raw))
            except (InvalidOperation, ValueError):
                return Decimal(0)
    return Decimal(0)


def _execution_echo(
    result: dict,
    *,
    ticker: str,
    side: str,
    price: str,
    count: str,
    post_only: bool | None,
    book: dict | None,
    notional: Decimal,
    corr_group: str | None,
    edge_net_cents: str | None,
    decision_id: str | None = None,
) -> dict:
    """Fee and role for the proof log. day_spend_remaining comes from the book just loaded."""
    out = dict(result)
    m_taker, m_maker, source = series_fees({"ticker": ticker})
    maker = post_only is True
    out["role"] = "maker" if maker else "taker"
    out["m_taker"] = format(m_taker, "f")
    out["m_maker"] = format(m_maker, "f")
    out["fee_m_source"] = source
    if maker:
        fee = maker_fee_cents(Decimal(price), Decimal(count), multiplier=m_maker)
        out["fee_cents_est"] = format(fee, "f")
        out["fee_note"] = (
            "maker ceil(M_maker * 0.0175 * contracts * P * (1 - P) * 100) cents; "
            f"M_maker={format(m_maker, 'f')} ({source})"
        )
    else:
        fee = fee_dome_cents(Decimal(price), Decimal(count), multiplier=m_taker)
        out["fee_cents_est"] = format(fee, "f")
        out["fee_note"] = (
            "taker ceil(M_taker * 0.07 * contracts * P * (1 - P) * 100) cents; "
            f"M_taker={format(m_taker, 'f')} ({source})"
        )
    if book is not None:
        caps = load_caps()
        daily = book.get("daily", Decimal(0))
        if notional > 0:
            daily = daily + notional
        remaining = (caps["daily"] - daily).quantize(Decimal("0.01"))
        if remaining < 0:
            remaining = Decimal("0.00")
        out["day_spend_remaining"] = format(remaining, "f")
    entry = Decimal(price) if side == "bid" else Decimal(1) - Decimal(price)
    out["flb_band"] = flb_band(entry)
    out["edge_net_cents"] = edge_net_cents
    out["corr_group"] = corr_group
    order_id = _response_order_id(result)
    out["order_id"] = order_id
    filled = _response_fill_count(result)
    if filled > 0 and isinstance(order_id, str):
        out["ledger_fill"] = _append_fill_ack(
            ticker=ticker,
            side="yes" if side == "bid" else "no",
            price=price,
            count=format(filled, "f"),
            order_id=order_id,
            corr_group=corr_group,
            edge_net_cents=edge_net_cents,
            fee_cents_est=out.get("fee_cents_est"),
            flb_band=out["flb_band"],
            decision_id=decision_id,
        )
    return out


def _append_fill_ack(**fields: object) -> dict:
    """Audit line for the >$2 history gate. The gate still reads Kalshi fills."""
    moment = datetime.now(ZoneInfo("America/Los_Angeles"))
    stamp = moment.strftime("%Y%m%d-%H%M%S")
    side = str(fields["side"])
    ticker = str(fields["ticker"])
    record = {
        "id": f"{stamp}-{ticker}-{side}-fill",
        "as_of": moment.isoformat(timespec="seconds"),
        "action": "fill",
        "ticker": ticker,
        "side": side,
        "order_id": fields["order_id"],
        "parent_id": fields.get("decision_id"),
        "price": fields["price"],
        "count": fields["count"],
        "fee_cents_est": fields.get("fee_cents_est"),
        "flb_band": fields.get("flb_band"),
        "edge_net_cents": fields.get("edge_net_cents"),
        "corr_group": fields.get("corr_group"),
        "notes": "place_order ack. Audit only. The $2 history gate still reads Kalshi fills.",
    }
    record = {key: value for key, value in record.items() if value is not None}
    try:
        written = record_fill(record)
    except RuntimeError as exc:
        text = str(exc)
        if "PRIVATE KEY" in text or "-----BEGIN" in text:
            text = "ledger append failed"
        return {"error": text, "mirror_path": MIRROR_LEDGER}
    return {"id": written["id"], "path": written["path"], "mirror_path": written["mirror_path"]}


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
    post_only = _optional_bool(args, "post_only")
    if post_only is True and time_in_force != "good_till_canceled":
        raise RuntimeError("post_only refuses a taker order; use time_in_force good_till_canceled")
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
    corr_group = _optional_group(args)
    if reduce_only is not True and corr_group is None:
        raise RuntimeError("corr_group is required on opening risk")
    edge_net_cents = _optional_edge(args)
    decision_id = _decision_id(args)
    book, notional = _guard_open(
        ticker=ticker,
        side=side,
        count=count,
        price=price,
        corr_group=corr_group,
        reduce_only=reduce_only,
    )
    try:
        result = auth_call("POST", "/portfolio/events/orders", body=body)
    except RuntimeError:
        raise
    if notional > 0:
        note_open(ticker=ticker, notional=notional, corr_group=corr_group, client_order_id=client_order_id)
    return _execution_echo(
        result,
        ticker=ticker,
        side=side,
        price=price,
        count=count,
        post_only=post_only,
        book=book,
        notional=notional,
        corr_group=corr_group,
        edge_net_cents=edge_net_cents,
        decision_id=decision_id,
    )


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
    corr_group = _optional_group(args)
    if corr_group is None:
        raise RuntimeError("corr_group is required on opening risk")
    book, notional = _guard_open(
        ticker=str(body["ticker"]),
        side=str(body["side"]),
        count=str(body["count"]),
        price=str(body["price"]),
        corr_group=corr_group,
        reduce_only=False,
    )
    result = auth_call("POST", _order_path(order_id, "/amend"), _subaccount_query(args) or None, body)
    return _execution_echo(
        result,
        ticker=str(body["ticker"]),
        side=str(body["side"]),
        price=str(body["price"]),
        count=str(body["count"]),
        post_only=None,
        book=book,
        notional=notional,
        corr_group=corr_group,
        edge_net_cents=_optional_edge(args),
        decision_id=_decision_id(args),
    )


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
    "The server never sets confirm. "
    "Opening risk is capped at a $2 default, a $15 hard max, 15% of the sleeve in one market, "
    "and 30% in one corr_group. corr_group is required when the order opens risk. "
    "Missing, empty, and none are refused. Mutually exclusive children share one group. "
    "Size above $2 needs a profitable fill history. "
    "Prices under 25 cents stay at $2. No withdraw or deposit."
)

MUTATING_TOOLS = [
    {
        "name": "place_order",
        "description": (
            "Place one Kalshi order (POST /portfolio/events/orders). "
            "Kalshi may return HTTP 403 when jurisdiction or market category blocks trading. "
            "The error text includes Kalshi's code and message. "
            "side is the YES book: bid buys YES, ask sells YES. "
            "price is a YES-side dollar string strictly between 0 and 1. "
            "count is contracts (fixed-point string or integer). "
            "time_in_force is fill_or_kill, good_till_canceled, or immediate_or_cancel. "
            "Recommended maker path: time_in_force good_till_canceled and post_only true. "
            "post_only with fill_or_kill or immediate_or_cancel is refused, and Kalshi rejects a post_only order that would cross. "
            "self_trade_prevention_type is taker_at_cross or maker. "
            "Pass client_order_id yourself if you need dedup. "
            "A successful place echoes role (maker or taker), fee_cents_est from the series catalog on the ticker, "
            "flb_band for the entry price, edge_net_cents when the caller passes it, corr_group, order_id, "
            "and day_spend_remaining from the live book. "
            "A response that already shows a fill appends one fill line to the decision ledger. "
            "That line is an audit trail. It does not raise the $2 history gate. The gate still reads Kalshi fills. "
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
                "corr_group": {
                    "type": "string",
                    "description": (
                        "Snake_case risk driver. Required on opening risk. "
                        "Missing, empty, and none are refused. "
                        "Mutually exclusive children of one parent share one group. "
                        "Examples: nfl_week_N, city_weather_YYYYMMDD, fed_meeting_YYYYMM. "
                        "Shares a 30% sleeve cap. A full co-resolution matrix is later."
                    ),
                },
                "edge_net_cents": {
                    "type": "number",
                    "description": "Echoed onto the response for the ledger. Omitted from the Kalshi body. Not computed here.",
                },
                "decision_id": {
                    "type": "string",
                    "description": "Optional parent id. Stored on the fill line when the ack shows a fill.",
                },
                "confirm": _CONFIRM,
            },
            "required": [
                "ticker",
                "side",
                "count",
                "price",
                "time_in_force",
                "self_trade_prevention_type",
                "corr_group",
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
                "corr_group": {
                    "type": "string",
                    "description": (
                        "Snake_case risk driver. Required because an amend is checked as opening risk. "
                        "Missing, empty, and none are refused. "
                        "Examples: nfl_week_N, city_weather_YYYYMMDD, fed_meeting_YYYYMM. "
                        "Shares a 30% sleeve cap."
                    ),
                },
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "ticker", "side", "price", "count", "corr_group", "confirm"],
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
