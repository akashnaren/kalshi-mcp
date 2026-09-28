"""Project official Kalshi payloads into the fields finance reads.

Quantity sign is part of the positions API: positive is YES, negative is NO.
Average, mark, and portfolio value are copied only when the API sends them.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

_MARKET_QTY = ("position_fp", "position")
_EVENT_QTY = ("total_cost_shares_fp", "total_cost_shares")
_FILL_QTY = ("count_fp", "count")
_AVG = ("average_price_dollars", "average_price", "avg_price_dollars", "avg_price")
_MARK = ("mark_price_dollars", "mark_price", "mark")


def _first(source: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in source and source[key] is not None:
            return source[key]
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except InvalidOperation:
            return None
    return None


def _side_from_qty(qty: object) -> str | None:
    number = _decimal(qty)
    if number is None or number == 0:
        return None
    return "yes" if number > 0 else "no"


def _dollars_from_cents(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def present_balance(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("unexpected balance payload")
    cents = raw.get("balance")
    if isinstance(cents, bool) or not isinstance(cents, int):
        raise RuntimeError("balance payload missing numeric balance")
    out = dict(raw)
    out["balance_cents"] = cents
    if not isinstance(out.get("balance_dollars"), str):
        out["balance_dollars"] = _dollars_from_cents(cents)
    out["cash"] = out["balance_dollars"]
    return out


def _with_price_aliases(out: dict) -> None:
    if "avg" not in out:
        avg = _first(out, _AVG)
        if avg is not None:
            out["avg"] = avg
    if "mark" not in out:
        mark = _first(out, _MARK)
        if mark is not None:
            out["mark"] = mark


def present_market_position(row: dict) -> dict:
    out = dict(row)
    qty = _first(out, _MARKET_QTY)
    if qty is not None and "qty" not in out:
        out["qty"] = qty
    if "side" not in out:
        side = _side_from_qty(out.get("qty"))
        if side is not None:
            out["side"] = side
    _with_price_aliases(out)
    return out


def present_event_position(row: dict) -> dict:
    out = dict(row)
    if "ticker" not in out and out.get("event_ticker"):
        out["ticker"] = out["event_ticker"]
    qty = _first(out, _EVENT_QTY)
    if qty is not None and "qty" not in out:
        out["qty"] = qty
    _with_price_aliases(out)
    return out


def present_positions(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("unexpected positions payload")
    out = dict(raw)
    markets = out.get("market_positions")
    if isinstance(markets, list):
        out["market_positions"] = [present_market_position(row) for row in markets if isinstance(row, dict)]
    events = out.get("event_positions")
    if isinstance(events, list):
        out["event_positions"] = [present_event_position(row) for row in events if isinstance(row, dict)]
    return out


def present_fill(row: dict) -> dict:
    out = dict(row)
    if "ticker" not in out and out.get("market_ticker"):
        out["ticker"] = out["market_ticker"]
    qty = _first(out, _FILL_QTY)
    if qty is not None and "qty" not in out:
        out["qty"] = qty
    if "side" not in out and out.get("outcome_side"):
        out["side"] = out["outcome_side"]
    return out


def present_fills(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise RuntimeError("unexpected fills payload")
    out = dict(raw)
    fills = out.get("fills")
    if isinstance(fills, list):
        out["fills"] = [present_fill(row) for row in fills if isinstance(row, dict)]
    return out
