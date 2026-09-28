"""The three read-only tools. No order, cancel, or transfer handlers exist."""

from __future__ import annotations

from kalshi_readonly import __version__
from kalshi_readonly.http import auth_get, public_get
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
]

HANDLERS = {
    "exchange_status": exchange_status,
    "list_markets": list_markets,
    "cash_or_positions": cash_or_positions,
}


def main() -> None:
    run_server("kalshi-readonly", __version__, TOOLS, HANDLERS)
