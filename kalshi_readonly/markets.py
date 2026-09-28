"""Bounded public market reads. One list call per page. Beliefs are one batched call.

Scan pages share one in-flight request so concurrent find_best_bets calls do
not stampede GET /markets. A later page that is rate limited keeps the pages
already fetched.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from kalshi_readonly.http import RateLimitedError, public_get

_TTL_SECONDS = 300.0
_PARTIAL_TTL_SECONDS = 60.0
_CACHE_CAP = 8
_FLIGHT_WAIT_SECONDS = 75.0
_KEEP = (
    "ticker",
    "event_ticker",
    "title",
    "yes_sub_title",
    "no_sub_title",
    "status",
    "market_type",
    "yes_ask_dollars",
    "no_ask_dollars",
    "yes_bid_dollars",
    "no_bid_dollars",
    "yes_ask",
    "no_ask",
    "yes_bid",
    "no_bid",
    "yes_ask_size_fp",
    "no_ask_size_fp",
    "yes_bid_size_fp",
    "volume_fp",
    "volume_24h_fp",
    "volume",
    "volume_24h",
    "close_time",
    "expected_expiration_time",
    "latest_expiration_time",
    "notional_value_dollars",
    "fee_multiplier",
    "series_fee_multiplier",
    "fee_multiplier_fp",
)

_cache: dict[str, tuple[float, list[dict], int, bool]] = {}
_lock = threading.Lock()


@dataclass(frozen=True)
class Loaded:
    markets: list[dict]
    pages: int
    requests: int
    cache: str
    rate_limited: bool = False


@dataclass
class _Flight:
    event: threading.Event
    result: Loaded | None = None
    error: BaseException | None = None


_flights: dict[str, _Flight] = {}


def _new_event() -> threading.Event:
    return threading.Event()


def clear_market_cache() -> None:
    _cache.clear()


def _slim(row: dict) -> dict:
    return {key: row[key] for key in _KEEP if key in row}


def _rows(data: dict) -> list[dict]:
    batch = data.get("markets")
    if not isinstance(batch, list):
        raise RuntimeError("unexpected markets payload")
    markets: list[dict] = []
    seen: set[str] = set()
    for row in batch:
        if not isinstance(row, dict):
            continue
        slim = _slim(row)
        ticker = slim.get("ticker")
        if isinstance(ticker, str) and ticker not in seen:
            seen.add(ticker)
            markets.append(slim)
    return markets


def _scan(max_pages: int, page_size: int) -> tuple[list[dict], int, int, bool]:
    markets: list[dict] = []
    seen: set[str] = set()
    pages = 0
    requests = 0
    cursor = ""
    while pages < max_pages:
        query: dict[str, object] = {
            "status": "open",
            "limit": page_size,
            "mve_filter": "exclude",
        }
        if cursor:
            query["cursor"] = cursor
        try:
            data = public_get("/markets", query)
        except RateLimitedError:
            if pages == 0:
                raise
            return markets, pages, requests, True
        requests += 1
        pages += 1
        for row in _rows(data):
            ticker = row["ticker"]
            if ticker not in seen:
                seen.add(ticker)
                markets.append(row)
        raw_cursor = data.get("cursor")
        if not isinstance(raw_cursor, str) or not raw_cursor:
            break
        cursor = raw_cursor
    return markets, pages, requests, False


def _by_tickers(tickers: list[str]) -> tuple[list[dict], int, int]:
    """One request. Do not follow cursor and do not fetch each ticker alone."""
    query = {"status": "open", "limit": 200, "tickers": ",".join(tickers)}
    data = public_get("/markets", query)
    return _rows(data), 1, 1


def _lookup(key: str) -> Loaded | None:
    hit = _cache.get(key)
    if hit is None or hit[0] <= time.monotonic():
        return None
    return Loaded(markets=list(hit[1]), pages=hit[2], requests=0, cache="hit", rate_limited=hit[3])


def _store(key: str, markets: list[dict], pages: int, rate_limited: bool) -> None:
    ttl = _PARTIAL_TTL_SECONDS if rate_limited else _TTL_SECONDS
    _cache[key] = (time.monotonic() + ttl, markets, pages, rate_limited)
    if len(_cache) > _CACHE_CAP:
        oldest = min(_cache, key=lambda item: _cache[item][0])
        if oldest != key:
            _cache.pop(oldest, None)


def _copy(loaded: Loaded, *, cache: str, requests: int) -> Loaded:
    return Loaded(
        markets=list(loaded.markets),
        pages=loaded.pages,
        requests=requests,
        cache=cache,
        rate_limited=loaded.rate_limited,
    )


def _fetch(key: str, *, tickers: list[str] | None, max_pages: int, page_size: int, scan: bool) -> Loaded:
    if scan:
        markets, pages, requests, rate_limited = _scan(max_pages, page_size)
    else:
        markets, pages, requests = _by_tickers(list(tickers or []))
        rate_limited = False
    _store(key, markets, pages, rate_limited)
    return Loaded(
        markets=list(markets),
        pages=pages,
        requests=requests,
        cache="miss",
        rate_limited=rate_limited,
    )


def load_markets(*, tickers: list[str] | None, max_pages: int, page_size: int, scan: bool) -> Loaded:
    if scan:
        key = f"scan|{page_size}|{max_pages}"
    else:
        key = "tickers|" + ",".join(tickers or [])
    cached = _lookup(key)
    if cached is not None:
        return cached
    with _lock:
        cached = _lookup(key)
        if cached is not None:
            return cached
        flight = _flights.get(key)
        if flight is None:
            flight = _Flight(event=_new_event())
            _flights[key] = flight
            leader = True
        else:
            leader = False
    if not leader:
        if not flight.event.wait(_FLIGHT_WAIT_SECONDS):
            raise RuntimeError("market scan timed out waiting for in-flight request")
        if flight.error is not None:
            raise flight.error
        if flight.result is None:
            raise RuntimeError("market scan failed")
        return _copy(flight.result, cache="hit", requests=0)
    try:
        loaded = _fetch(key, tickers=tickers, max_pages=max_pages, page_size=page_size, scan=scan)
        flight.result = loaded
        return loaded
    except BaseException as exc:
        flight.error = exc
        raise
    finally:
        with _lock:
            if _flights.get(key) is flight:
                _flights.pop(key, None)
        flight.event.set()
