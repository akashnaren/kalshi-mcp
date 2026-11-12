"""Live series fee schedule. The prefix catalog is the fallback.

GET /series/{ticker} is the source for fee_type and fee_multiplier.
https://docs.kalshi.com/api-reference/market/get-series

The formulas are the published schedule.
https://kalshi.com/docs/kalshi-fee-schedule.pdf

quadratic uses the general table and has no maker fee.
quadratic_with_maker_fees uses that table plus the maker section (0.0175).
quadratic_with_combo_maker_fees uses a 0.5 maker factor instead of 0.25.
flat is the specific table, which fee_multiplier does not encode, so a flat
series falls through to the prefix catalog.
"""

from __future__ import annotations

import threading
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from kalshi_readonly.http import public_get

_TAKER_TYPES = frozenset({
    "quadratic",
    "quadratic_with_maker_fees",
    "quadratic_with_combo_maker_fees",
})
# Maker formula is ceil(M * 0.0175 * C * P * (1-P)). 0.0175 is 0.25 * 0.07.
# Combo markets use 0.5 instead of 0.25, so M is doubled for that formula.
_MAKER_FACTOR = {
    "quadratic": Decimal(0),
    "quadratic_with_maker_fees": Decimal(1),
    "quadratic_with_combo_maker_fees": Decimal(2),
}

_cache: dict[str, dict | None] = {}
_lock = threading.Lock()


def clear_series_fee_cache() -> None:
    with _lock:
        _cache.clear()


def fetch_series(series_ticker: str) -> dict:
    """GET /series/{series_ticker}. Tests replace this. No order calls."""
    path = "/series/" + urllib.parse.quote(series_ticker, safe="")
    return public_get(path)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _waiver_active(value: object, now: datetime) -> bool:
    """fee_waiver_expiration_time is on the market, and on series when sent."""
    expires = _parse_time(value)
    return expires is not None and expires > now


def _multiplier(raw: object) -> Decimal | None:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        if isinstance(raw, str):
            value = Decimal(raw.strip())
        elif isinstance(raw, int):
            value = Decimal(raw)
        elif isinstance(raw, float):
            value = Decimal(str(raw))
        else:
            return None
    except (InvalidOperation, ValueError):
        return None
    if value < 0 or value > 100:
        return None
    return value


def _series_ticker(market: dict) -> str | None:
    raw = market.get("series_ticker")
    if isinstance(raw, str) and raw.strip():
        return raw.strip().upper()
    for key in ("event_ticker", "ticker"):
        value = market.get(key)
        if isinstance(value, str) and "-" in value:
            head = value.split("-", 1)[0].strip()
            if head:
                return head.upper()
    return None


def _body(payload: object) -> dict | None:
    if not isinstance(payload, dict):
        return None
    series = payload.get("series")
    if isinstance(series, dict):
        return series
    if "fee_type" in payload:
        return payload
    return None


def _load(series_ticker: str) -> dict | None:
    key = series_ticker.upper()
    with _lock:
        if key in _cache:
            return _cache[key]
    try:
        payload = fetch_series(key)
    except (RuntimeError, OSError):
        with _lock:
            _cache[key] = None
        return None
    body = _body(payload)
    with _lock:
        _cache[key] = body
    return body


def _from_type(fee_type: str, multiplier: Decimal) -> tuple[Decimal, Decimal, str] | None:
    if fee_type not in _TAKER_TYPES:
        return None
    factor = _MAKER_FACTOR[fee_type]
    return multiplier, multiplier * factor, "series"


def live_schedule(market: dict | None, now: datetime) -> tuple[Decimal, Decimal, str] | None:
    """(M_taker, M_maker, source) from the series, or None when the caller should guess.

    A fee waiver that has not expired zeroes both sides. quadratic maker M is 0.
    The result is cached for the process so one run does not repeat the GET.
    """
    if not isinstance(market, dict):
        return None
    if _waiver_active(market.get("fee_waiver_expiration_time"), now):
        return Decimal(0), Decimal(0), "waiver"
    ticker = _series_ticker(market)
    if ticker is None:
        return None
    body = _load(ticker)
    if body is None:
        return None
    if _waiver_active(body.get("fee_waiver_expiration_time"), now):
        return Decimal(0), Decimal(0), "waiver"
    fee_type = body.get("fee_type")
    multiplier = _multiplier(body.get("fee_multiplier"))
    if not isinstance(fee_type, str) or multiplier is None:
        return None
    return _from_type(fee_type.strip().lower(), multiplier)
