"""Read tools plus confirm-gated order tools.

Mutating tools are omitted from the advertised list while KALSHI_SAFE_MODE is
on (the default). Handlers still refuse if something calls them anyway.
There is no withdraw, deposit, or strategy tool.
"""

from __future__ import annotations

import urllib.parse

from kalshi_readonly import __version__
from kalshi_readonly.attestation import load_washington_attestation
from kalshi_readonly.guard import safe_mode_enabled
from kalshi_readonly.http import auth_get, public_get
from kalshi_readonly.report import present_balance, present_fills, present_positions
from kalshi_readonly.stdio import run_server
from kalshi_readonly.ledger import (
    APPEND_DECISION_TOOL,
    SUMMARIZE_DECISIONS_TOOL,
    append_decision,
    summarize_decisions,
)
from kalshi_readonly.recommend import FIND_BEST_TOOL, find_best_bets
from kalshi_readonly.routine import fe_routine
from kalshi_readonly.trade import (
    MUTATING_HANDLERS,
    MUTATING_TOOLS,
    OPEN_ORDERS_TOOL,
    list_open_orders,
)

_INCLUDES = ("balance", "cash", "positions", "both", "fills")
_MVE_PAGES = 4
_CATEGORY_PREFIXES = {
    "sports": ("KXNFL", "KXNBA", "KXNHL", "KXMLB", "KXNCAAF", "KXNCAAB", "KXEPL", "KXUCL", "KXUFC", "KXUSL", "KXINTL"),
    "weather": ("KXHIGH", "KXLOWT", "KXLOW", "KXRAIN", "KXSNOW", "KXHURR", "KXTEMP"),
    "politics": ("KXPRES", "KXSENATE", "KXHOUSE", "KXGOV", "KXELECT"),
    "crypto": ("KXBTC", "KXETH"),
    "macro": ("KXFED", "KXCPI", "KXGDP", "KXPAYROLL", "KXUNEMP", "KXJOB", "KXFOMC"),
    "other": (),
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


def exchange_status(_args: dict | None = None) -> dict:
    """Public exchange status plus the Washington attestation note, when one is stored."""
    payload = public_get("/exchange/status")
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected exchange status payload")
    out = dict(payload)
    out.update(load_washington_attestation())
    return out


def _optional_text(args: dict, field: str, limit: int) -> str | None:
    if field not in args or args[field] in (None, ""):
        return None
    value = args[field]
    if not isinstance(value, str) or len(value) > limit or any(char in value for char in "\r\n"):
        raise RuntimeError(f"{field} must be a short string")
    if "PRIVATE KEY" in value or "-----BEGIN" in value:
        raise RuntimeError(f"{field} must be a short string")
    return value


def _blank_quote(market: dict, *keys: str) -> bool:
    for key in keys:
        value = market.get(key)
        if value not in (None, ""):
            return False
    return True


def _null_book(market: dict) -> bool:
    ask = _blank_quote(market, "yes_ask", "yes_ask_dollars")
    bid = _blank_quote(market, "yes_bid", "yes_bid_dollars")
    return ask and bid


def _is_mve(market: dict) -> bool:
    for key in ("ticker", "event_ticker", "series_ticker"):
        value = market.get(key)
        if isinstance(value, str) and value.upper().startswith("KXMVE"):
            return True
    return False


def _category_match(market: dict, category: str) -> bool:
    prefixes = _CATEGORY_PREFIXES[category]
    if not prefixes:
        return True
    for key in ("series_ticker", "ticker", "event_ticker"):
        value = market.get(key)
        if isinstance(value, str) and value.upper().startswith(prefixes):
            return True
    return False


_SETTLEMENT_FIELDS = (
    "settlement_value",
    "settlement_value_dollars",
    "settlement_ts",
    "expiration_value",
)


def _compact_market(market: dict) -> dict:
    row = {
        "ticker": market.get("ticker"),
        "title": market.get("title"),
        "status": market.get("status"),
        "result": market.get("result"),
        "yes_bid": market.get("yes_bid"),
        "yes_ask": market.get("yes_ask"),
        "yes_bid_dollars": market.get("yes_bid_dollars"),
        "yes_ask_dollars": market.get("yes_ask_dollars"),
        "no_bid": market.get("no_bid"),
        "no_ask": market.get("no_ask"),
        "volume": market.get("volume"),
        "volume_24h": market.get("volume_24h"),
        "close_time": market.get("close_time"),
        "series_ticker": market.get("series_ticker"),
        "event_ticker": market.get("event_ticker"),
    }
    for key in _SETTLEMENT_FIELDS:
        if key in market:
            row[key] = market.get(key)
    return row


def list_markets(args: dict | None = None) -> dict:
    """Public markets, defaulting to open non-MVE rows that have a yes quote.

    Kalshi's first page is often KXMVECROSSCATEGORY with null books. The default
    query sends mve_filter=exclude and status=open, then drops a row whose yes
    bid and yes ask are both missing. A page that is entirely empty is followed,
    up to a few pages, so a short list can still return a liquid single.
    category is a client prefix filter. It is not a Kalshi query parameter.
    ticker is one market: GET /markets/{ticker}. That row is returned even when
    the book is empty, so a settlement job can read result.
    """
    args = args or {}
    limit = _limit(args, 25)
    status = args.get("status", "open")
    if status in (None, ""):
        status = "open"
    if not isinstance(status, str) or status not in {"open", "unopened", "closed", "settled", "paused", "any"}:
        raise RuntimeError("status must be open, unopened, paused, closed, settled, or any")
    mve = args.get("mve_filter", "exclude")
    if mve in (None, ""):
        mve = "exclude"
    if not isinstance(mve, str) or mve not in {"exclude", "only", "all"}:
        raise RuntimeError("mve_filter must be exclude, only, or all")
    liquid = args.get("liquid", True)
    if not isinstance(liquid, bool):
        raise RuntimeError("liquid must be a boolean")
    category = _optional_text(args, "category", 32)
    if category is not None:
        category = category.lower()
        if category not in _CATEGORY_PREFIXES:
            raise RuntimeError("category must be sports, weather, politics, crypto, macro, or other")
    cursor = _optional_text(args, "cursor", 1024)
    ticker = _optional_text(args, "ticker", 128)
    filters = {
        "status": status,
        "mve_filter": mve,
        "liquid": liquid,
        "category": category,
        "ticker": ticker,
    }
    if ticker is not None:
        data = public_get("/markets/" + urllib.parse.quote(ticker, safe=""))
        market = data.get("market") if isinstance(data, dict) else None
        if not isinstance(market, dict):
            raise RuntimeError("unexpected market payload")
        return {
            "count": 1,
            "markets": [_compact_market(market)],
            "cursor": "",
            "pages_fetched": 1,
            "dropped_null_book": 0,
            "dropped_mve": 0,
            "filters": filters,
        }
    kept: list[dict] = []
    dropped_null = 0
    dropped_mve = 0
    pages = 0
    next_cursor = ""
    while pages < _MVE_PAGES and len(kept) < limit:
        query: dict[str, object] = {"limit": limit}
        if status != "any":
            query["status"] = status
        if mve != "all":
            query["mve_filter"] = mve
        series = _optional_text(args, "series_ticker", 128)
        if series is not None:
            query["series_ticker"] = series
        event = _optional_text(args, "event_ticker", 128)
        if event is not None:
            query["event_ticker"] = event
        if cursor:
            query["cursor"] = cursor
        data = public_get("/markets", query)
        pages += 1
        markets = data.get("markets") or []
        page_kept = 0
        if not isinstance(markets, list):
            raise RuntimeError("unexpected markets payload")
        for market in markets:
            if not isinstance(market, dict):
                continue
            if mve != "only" and _is_mve(market):
                dropped_mve += 1
                continue
            if category is not None and not _category_match(market, category):
                continue
            if liquid and _null_book(market):
                dropped_null += 1
                continue
            kept.append(_compact_market(market))
            page_kept += 1
            if len(kept) >= limit:
                break
        raw_cursor = data.get("cursor")
        next_cursor = raw_cursor if isinstance(raw_cursor, str) else ""
        if page_kept or not next_cursor or len(kept) >= limit:
            break
        cursor = next_cursor
    return {
        "count": len(kept),
        "markets": kept[:limit],
        "cursor": next_cursor,
        "pages_fetched": pages,
        "dropped_null_book": dropped_null,
        "dropped_mve": dropped_mve,
        "filters": filters,
    }


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


READ_TOOLS = [
    {
        "name": "exchange_status",
        "description": (
            "Public Kalshi exchange status. "
            "Also reports the Washington region attestation from the note on disk: "
            "expires_at, expires_in_hours, and expired. "
            "washington_attestation is null and washington_attestation_reason explains why "
            "when that note is missing or has no expires_at. Does not invent an expiry."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "list_markets",
        "description": (
            "List public Kalshi markets for scouting liquid singles. "
            "Default status is open, mve_filter is exclude, liquid is true, and limit is 25. "
            "Rows with a null yes bid and a null yes ask are dropped. "
            "A page that is only multivariate combos or null books is followed, up to 4 pages. "
            "cursor is the Kalshi cursor after the last page read. "
            "Optional series_ticker and event_ticker are sent to GET /markets. "
            "ticker is one market and is fetched with GET /markets/{ticker} (Kalshi's single-market read; "
            "the list filter name is tickers). That row includes result and any settlement fields Kalshi sent "
            "(settlement_value, settlement_value_dollars, settlement_ts, expiration_value), including a settled market with a null book. "
            "List rows include result and those settlement fields when Kalshi sent them. "
            "category is sports, weather, politics, crypto, macro, or other and is applied here, not by Kalshi. "
            "Pass mve_filter all to include multivariate markets. Pass liquid false to keep null books. "
            "Pass status any to omit the status filter."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "1 to 1000. Default 25."},
                "status": {
                    "type": "string",
                    "description": "open (default), unopened, paused, closed, settled, or any.",
                },
                "ticker": {
                    "type": "string",
                    "description": "One market ticker. Fetched with GET /markets/{ticker}, including settled results.",
                },
                "cursor": {"type": "string"},
                "series_ticker": {"type": "string"},
                "event_ticker": {"type": "string"},
                "mve_filter": {
                    "type": "string",
                    "description": "exclude (default), only, or all. all is not sent to Kalshi.",
                },
                "liquid": {
                    "type": "boolean",
                    "description": "Drop rows with no yes bid and no yes ask. Default true.",
                },
                "category": {
                    "type": "string",
                    "description": "Client prefix filter: sports, weather, politics, crypto, macro, or other.",
                },
            },
        },
    },
    FIND_BEST_TOOL,
    APPEND_DECISION_TOOL,
    SUMMARIZE_DECISIONS_TOOL,
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
        "name": "fe_routine",
        "description": (
            "Finance Engineer daily and end-of-day routine. Read only. "
            "Does not place, cancel, amend, or decrease. "
            "Use with find_best_bets, then place_order only when KALSHI_SAFE_MODE=0, confirm is true, and the caps allow it."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
]

HANDLERS = {
    "exchange_status": exchange_status,
    "list_markets": list_markets,
    "find_best_bets": find_best_bets,
    "append_decision": append_decision,
    "summarize_decisions": summarize_decisions,
    "fe_routine": fe_routine,
    "cash_or_positions": cash_or_positions,
    "list_open_orders": list_open_orders,
    **MUTATING_HANDLERS,
}


def registered_tools() -> list[dict]:
    """Tools advertised on tools/list. Mutations appear only when safe mode is off."""
    tools = [*READ_TOOLS, OPEN_ORDERS_TOOL]
    if not safe_mode_enabled():
        tools.extend(MUTATING_TOOLS)
    return tools


def main() -> None:
    run_server("kalshi-readonly", __version__, registered_tools, HANDLERS)
