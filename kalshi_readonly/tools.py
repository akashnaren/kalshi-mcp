"""Read tools plus confirm-gated order tools. No deposit, withdraw, or strategy."""

from __future__ import annotations

from kalshi_readonly import __version__
from kalshi_readonly.gates import TRADE_WRITES, safe_mode
from kalshi_readonly.http import auth_get, public_get
from kalshi_readonly.orders import amend_order, cancel_order, decrease_order, list_open_orders, place_order
from kalshi_readonly.report import present_balance, present_fills, present_positions
from kalshi_readonly.stdio import run_server

_INCLUDES = ("balance", "cash", "positions", "both", "fills")


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


def exchange_status(_args: dict | None = None) -> dict:
    return public_get("/exchange/status")


def list_markets(args: dict | None = None) -> dict:
    args = args or {}
    query: dict[str, object] = {"limit": _limit(args, 5)}
    if args.get("status"):
        query["status"] = args["status"]
    if args.get("ticker"):
        query["ticker"] = args["ticker"]
    data = public_get("/markets", query)
    markets = data.get("markets") or []
    compact = [
        {
            "ticker": market.get("ticker"),
            "title": market.get("title"),
            "status": market.get("status"),
            "yes_bid": market.get("yes_bid"),
            "yes_ask": market.get("yes_ask"),
            "volume": market.get("volume"),
        }
        for market in markets
        if isinstance(market, dict)
    ]
    return {"count": len(compact), "markets": compact, "cursor": data.get("cursor")}


def cash_or_positions(args: dict | None = None) -> dict:
    args = args or {}
    want = str(args.get("include") or "both").strip().lower()
    if want not in _INCLUDES:
        raise RuntimeError("include must be balance, cash, positions, both, or fills")
    limit = _limit(args, 50)
    out: dict = {}
    if want in ("balance", "both", "cash"):
        out["balance"] = present_balance(auth_get("/portfolio/balance"))
    if want in ("positions", "both"):
        out["positions"] = present_positions(auth_get("/portfolio/positions", {"limit": limit}))
    if want == "fills":
        out["fills"] = present_fills(auth_get("/portfolio/fills", {"limit": limit}))
    return out


_CONFIRM = {
    "type": "boolean",
    "description": "Must be true. Ask the operator before setting this. The server never sets it.",
}

TOOLS = [
    {
        "name": "exchange_status",
        "description": "Public Kalshi exchange status.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_markets",
        "description": "List Kalshi markets (public).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "status": {"type": "string"},
                "ticker": {"type": "string"},
            },
        },
    },
    {
        "name": "cash_or_positions",
        "description": (
            "Read-only Kalshi cash and positions. Requires API key env. "
            "include: balance, cash, positions, both (default), or fills. "
            "Balance includes balance_cents, balance_dollars, cash, and portfolio_value when returned. "
            "Positions include market_positions and event_positions with ticker, side, and qty. "
            "avg and mark are present only when the API sends them. Does not trade."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
    {
        "name": "list_open_orders",
        "description": (
            "Read resting Kalshi orders. Requires API key env. "
            "Optional ticker, limit, and cursor. Does not place, cancel, amend, or decrease. "
            "Stays available while KALSHI_SAFE_MODE is on."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "ticker": {"type": "string"},
                "limit": {"type": "integer"},
                "cursor": {"type": "string"},
            },
        },
    },
    {
        "name": "place_order",
        "description": (
            "Place one Kalshi limit or immediate order (POST /portfolio/events/orders). "
            "side is bid (buy YES) or ask (sell YES). price is a fixed-point dollar string such as \"0.5600\". "
            "count is a contract count. Hidden until KALSHI_SAFE_MODE=0. Requires confirm: true after an explicit human ask. "
            "The server never sets confirm. No deposit, withdraw, or strategy."
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
                "cancel_order_on_pause": {"type": "boolean"},
                "reduce_only": {"type": "boolean"},
                "subaccount": {"type": "integer"},
                "exchange_index": {"type": "integer"},
                "order_group_id": {"type": "string"},
                "confirm": _CONFIRM,
            },
            "required": ["ticker", "side", "count", "price", "confirm"],
        },
    },
    {
        "name": "cancel_order",
        "description": (
            "Cancel one resting Kalshi order (DELETE /portfolio/events/orders/{order_id}). "
            "ticker is the market ticker used to route the cancel. "
            "Hidden until KALSHI_SAFE_MODE=0. Requires confirm: true after an explicit human ask. "
            "The server never sets confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "ticker": {"type": "string"},
                "subaccount": {"type": "integer"},
                "exchange_index": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "ticker", "confirm"],
        },
    },
    {
        "name": "amend_order",
        "description": (
            "Amend price and/or max fillable count (POST /portfolio/events/orders/{order_id}/amend). "
            "count is filled plus the desired resting remainder, not the remainder alone. "
            "Decreasing size keeps queue position; a price change or a larger size does not. "
            "Hidden until KALSHI_SAFE_MODE=0. Requires confirm: true after an explicit human ask. "
            "The server never sets confirm."
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
            "Reduce a resting order (POST /portfolio/events/orders/{order_id}/decrease). "
            "Pass exactly one of reduce_by or reduce_to. "
            "Hidden until KALSHI_SAFE_MODE=0. Requires confirm: true after an explicit human ask. "
            "The server never sets confirm."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "ticker": {"type": "string"},
                "reduce_by": {"type": "string"},
                "reduce_to": {"type": "string"},
                "exchange_index": {"type": "integer"},
                "subaccount": {"type": "integer"},
                "confirm": _CONFIRM,
            },
            "required": ["order_id", "confirm"],
        },
    },
]

HANDLERS = {
    "exchange_status": exchange_status,
    "list_markets": list_markets,
    "cash_or_positions": cash_or_positions,
    "list_open_orders": list_open_orders,
    "place_order": place_order,
    "cancel_order": cancel_order,
    "amend_order": amend_order,
    "decrease_order": decrease_order,
}


def visible_tools() -> list[dict]:
    """Omit trade writes while safe mode is on so the catalog matches what can run."""
    if safe_mode():
        return [tool for tool in TOOLS if tool["name"] not in TRADE_WRITES]
    return list(TOOLS)


def main() -> None:
    run_server("kalshi-readonly", __version__, visible_tools, HANDLERS)
