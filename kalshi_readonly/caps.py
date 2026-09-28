"""In-process sleeve caps for opening risk. Cancels and decreases do not add risk.

Default stake is $2. The hard ceiling is $15 and cannot be raised by env.
Size above the default requires a profitable Kalshi fill history.
One market is at most 15% of the sleeve. One corr_group is at most 30%.
"""

from __future__ import annotations

import os
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN

HARD_TRADE = Decimal("15")
DEFAULT_TRADE = Decimal("2")
SLEEVE = Decimal("71")
MARKET_FRACTION = Decimal("0.15")
GROUP_FRACTION = Decimal("0.30")
DAILY = Decimal("10")
MIN_CLOSED = 3
FIXED_PRICE = Decimal("0.25")
MODEST_PRICE = Decimal("0.50")
MODEST_CAP = Decimal("8")
_CENT = Decimal("0.01")
_LEDGER: list[dict] = []
_LOCK = threading.Lock()


def reset_ledger() -> None:
    with _LOCK:
        _LEDGER.clear()


def note_open(*, ticker: str, notional: Decimal, corr_group: str | None, client_order_id: str | None) -> None:
    if notional <= 0:
        return
    with _LOCK:
        _LEDGER.append(
            {
                "ticker": ticker,
                "notional": notional,
                "corr_group": corr_group,
                "client_order_id": client_order_id,
                "at": datetime.now(timezone.utc),
            }
        )


def _env_decimal(name: str, default: Decimal, low: Decimal, high: Decimal) -> Decimal:
    raw = os.environ.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation:
        return default
    if value < low or value > high:
        return default
    return value


def load_caps() -> dict[str, Decimal]:
    ceiling = _env_decimal("KALSHI_MAX_DOLLARS_PER_TRADE", HARD_TRADE, _CENT, HARD_TRADE)
    default = _env_decimal("KALSHI_DEFAULT_DOLLARS_PER_TRADE", DEFAULT_TRADE, _CENT, HARD_TRADE)
    if default > ceiling:
        default = ceiling
    return {
        "sleeve": _env_decimal("KALSHI_SLEEVE_DOLLARS", SLEEVE, Decimal("1"), Decimal("1000000")),
        "default": default,
        "ceiling": ceiling,
        "market_fraction": _env_decimal("KALSHI_MAX_SLEEVE_FRACTION", MARKET_FRACTION, Decimal("0.01"), Decimal("1")),
        "group_fraction": _env_decimal("KALSHI_MAX_GROUP_FRACTION", GROUP_FRACTION, Decimal("0.01"), GROUP_FRACTION),
        "daily": _env_decimal("KALSHI_MAX_DAILY_NOTIONAL", DAILY, _CENT, Decimal("1000000")),
    }


def stake_mode(price: Decimal) -> str:
    if price < FIXED_PRICE:
        return "fixed_2"
    if price >= MODEST_PRICE:
        return "modest"
    return "kelly"


def mode_cap(mode: str, allows_scale: bool, caps: dict[str, Decimal]) -> Decimal:
    if mode == "fixed_2":
        return min(DEFAULT_TRADE, caps["ceiling"])
    if mode == "modest":
        return min(MODEST_CAP if allows_scale else caps["default"], caps["ceiling"])
    if allows_scale:
        return caps["ceiling"]
    return min(caps["default"], caps["ceiling"])


def _money(value: object, dollar_key: str, cent_key: str) -> Decimal | None:
    if not isinstance(value, dict):
        return None
    raw = value.get(dollar_key)
    if raw not in (None, ""):
        try:
            return Decimal(str(raw))
        except InvalidOperation:
            return None
    cents = value.get(cent_key)
    if isinstance(cents, bool) or cents in (None, ""):
        return None
    try:
        return (Decimal(str(cents)) / Decimal(100)).quantize(_CENT)
    except InvalidOperation:
        return None


def _rows(payload: dict, *keys: str) -> list:
    if not isinstance(payload, dict):
        return []
    for key in keys:
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _day(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, str) and len(value) >= 10:
        return value[:10]
    return None


def assess_history(positions: dict, fills: dict) -> dict:
    pos_rows = _rows(positions, "market_positions", "positions")
    fill_rows = _rows(fills, "fills")
    sells = sum(1 for row in fill_rows if str(row.get("action") or "").lower() == "sell")
    net = Decimal(0)
    realized = 0
    saw = False
    for row in pos_rows:
        pnl = _money(row, "realized_pnl_dollars", "realized_pnl")
        if pnl is None:
            continue
        saw = True
        if pnl != 0:
            realized += 1
        fees = _money(row, "fees_paid_dollars", "fees_paid") or Decimal(0)
        net += pnl - fees
    closed = max(sells, realized if saw else 0)
    return {
        "closed_trades": closed,
        "net_realized": net.quantize(_CENT),
        "allows_scale": closed >= MIN_CLOSED and net > 0,
    }


def _order_notional(order: dict) -> Decimal:
    count_raw = order.get("remaining_count_fp") or order.get("remaining_count") or order.get("count")
    try:
        count = Decimal(str(count_raw))
    except (InvalidOperation, TypeError):
        return Decimal(0)
    price = _money(order, "yes_price_dollars", "yes_price")
    if price is None or count <= 0:
        return Decimal(0)
    side = str(order.get("side") or order.get("book_side") or "").lower()
    if side in {"no", "ask"}:
        return (count * (Decimal(1) - price)).quantize(_CENT, rounding=ROUND_DOWN)
    return (count * price).quantize(_CENT, rounding=ROUND_DOWN)


def _exposure(row: dict) -> Decimal:
    dollars = _money(row, "market_exposure_dollars", "market_exposure")
    if dollars is None:
        return Decimal(0)
    return abs(dollars)


def summarize_book(positions: dict, fills: dict, orders: dict, *, now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    today = moment.astimezone(timezone.utc).date().isoformat()
    by_ticker: dict[str, Decimal] = {}
    seen: set[str] = set()
    daily = Decimal(0)
    for row in _rows(positions, "market_positions", "positions"):
        ticker = row.get("ticker")
        if isinstance(ticker, str):
            by_ticker[ticker] = by_ticker.get(ticker, Decimal(0)) + _exposure(row)
    for order in _rows(orders, "orders"):
        status = str(order.get("status") or "resting").lower()
        if status not in {"resting", "open", "pending"}:
            continue
        action = str(order.get("action") or "").lower()
        if action == "sell":
            continue
        client_id = order.get("client_order_id")
        if isinstance(client_id, str):
            seen.add(client_id)
        notional = _order_notional(order)
        ticker = order.get("ticker")
        if isinstance(ticker, str):
            by_ticker[ticker] = by_ticker.get(ticker, Decimal(0)) + notional
        if _day(order.get("created_time") or order.get("created_at")) == today:
            daily += notional
    for fill in _rows(fills, "fills"):
        if str(fill.get("action") or "").lower() == "sell":
            continue
        if _day(fill.get("created_time") or fill.get("created_at")) != today:
            continue
        client_id = fill.get("client_order_id")
        if isinstance(client_id, str):
            seen.add(client_id)
        price = _money(fill, "yes_price_dollars", "yes_price")
        try:
            count = Decimal(str(fill.get("count_fp") or fill.get("count") or "0"))
        except InvalidOperation:
            count = Decimal(0)
        if price is None or count <= 0:
            continue
        side = str(fill.get("side") or "yes").lower()
        if side == "no":
            daily += count * (Decimal(1) - price)
        else:
            daily += count * price
    groups: dict[str, Decimal] = {}
    with _LOCK:
        entries = list(_LEDGER)
    for entry in entries:
        if _day(entry["at"]) != today:
            continue
        client_id = entry.get("client_order_id")
        if isinstance(client_id, str) and client_id in seen:
            continue
        notional = entry["notional"]
        daily += notional
        ticker = entry["ticker"]
        by_ticker[ticker] = by_ticker.get(ticker, Decimal(0)) + notional
        group = entry.get("corr_group")
        if group:
            groups[group] = groups.get(group, Decimal(0)) + notional
    return {
        "by_ticker": {key: value.quantize(_CENT) for key, value in by_ticker.items()},
        "by_group": {key: value.quantize(_CENT) for key, value in groups.items()},
        "daily": daily.quantize(_CENT),
        "history": assess_history(positions, fills),
    }


def opening_notional(side: str, count: Decimal, price: Decimal) -> Decimal:
    if side == "bid":
        return (count * price).quantize(_CENT, rounding=ROUND_DOWN)
    if side == "ask":
        return (count * (Decimal(1) - price)).quantize(_CENT, rounding=ROUND_DOWN)
    raise RuntimeError("side must be bid or ask")


def enforce_open(
    *,
    notional: Decimal,
    price: Decimal,
    ticker: str,
    book: dict,
    corr_group: str | None = None,
) -> None:
    """Raise RuntimeError when new risk breaks a cap. Zero notional is a close."""
    if notional <= 0:
        return
    caps = load_caps()
    history = book.get("history") or {}
    allows = history.get("allows_scale") is True
    mode = stake_mode(price)
    sized = notional.quantize(_CENT)
    reasons: list[str] = []
    if sized > HARD_TRADE:
        reasons.append(f"hard max ${HARD_TRADE} per trade")
    elif sized > caps["ceiling"]:
        reasons.append(f"trade ceiling is ${caps['ceiling']}")
    else:
        allowed = mode_cap(mode, allows, caps)
        if sized > allowed and not allows and sized > caps["default"]:
            reasons.append(
                f"size stays at the ${caps['default']} default until fill history shows the sleeve is profitable"
            )
        elif sized > allowed and mode == "fixed_2":
            reasons.append(f"STAKE_MODE fixed_2 keeps this price at ${allowed}")
        elif sized > allowed and mode == "modest":
            reasons.append(f"STAKE_MODE modest caps this price at ${allowed}")
        elif sized > allowed:
            reasons.append(
                f"size stays at the ${caps['default']} default until fill history shows the sleeve is profitable"
            )
    market_cap = (caps["sleeve"] * caps["market_fraction"]).quantize(_CENT, rounding=ROUND_DOWN)
    market_now = book.get("by_ticker", {}).get(ticker, Decimal(0))
    market_after = (market_now + sized).quantize(_CENT)
    if market_after > market_cap:
        reasons.append(f"market would be ${market_after}, cap is ${market_cap} (15% of the sleeve)")
    if market_after >= caps["sleeve"]:
        reasons.append("refusing all-in: one market cannot take the whole sleeve")
    if corr_group:
        group_cap = (caps["sleeve"] * caps["group_fraction"]).quantize(_CENT, rounding=ROUND_DOWN)
        group_now = book.get("by_group", {}).get(corr_group, Decimal(0))
        group_after = (group_now + sized).quantize(_CENT)
        if group_after > group_cap:
            reasons.append(f"corr_group {corr_group} would be ${group_after}, cap is ${group_cap}")
    daily_after = (book.get("daily", Decimal(0)) + sized).quantize(_CENT)
    if daily_after > caps["daily"]:
        reasons.append(f"daily notional would be ${daily_after}, cap is ${caps['daily']}")
    if reasons:
        raise RuntimeError("refusing: " + "; ".join(reasons))
