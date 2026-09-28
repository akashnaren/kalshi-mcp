"""Bounded public market reads. One list call per page. Beliefs are one batched call."""

from __future__ import annotations

import time
from dataclasses import dataclass

from kalshi_readonly.http import public_get

_TTL_SECONDS = 45.0
_CACHE_CAP = 8
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
    "yes_ask",
    "no_ask",
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
)

_cache: dict[str, tuple[float, list[dict], int]] = {}


@dataclass(frozen=True)
class Loaded:
    markets: list[dict]
    pages: int
    requests: int
    cache: str


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


def _scan(max_pages: int, page_size: int) -> tuple[list[dict], int, int]:
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
        data = public_get("/markets", query)
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
    return markets, pages, requests


def _by_tickers(tickers: list[str]) -> tuple[list[dict], int, int]:
    """One request. Do not follow cursor and do not fetch each ticker alone."""
    query = {"status": "open", "limit": 200, "tickers": ",".join(tickers)}
    data = public_get("/markets", query)
    return _rows(data), 1, 1


def load_markets(*, tickers: list[str] | None, max_pages: int, page_size: int, scan: bool) -> Loaded:
    if scan:
        key = f"scan|{page_size}|{max_pages}"
    else:
        key = "tickers|" + ",".join(tickers or [])
    now = time.monotonic()
    hit = _cache.get(key)
    if hit is not None and hit[0] > now:
        return Loaded(markets=list(hit[1]), pages=hit[2], requests=0, cache="hit")
    if scan:
        markets, pages, requests = _scan(max_pages, page_size)
    else:
        markets, pages, requests = _by_tickers(list(tickers or []))
    _cache[key] = (now + _TTL_SECONDS, markets, pages)
    if len(_cache) > _CACHE_CAP:
        oldest = min(_cache, key=lambda item: _cache[item][0])
        if oldest != key:
            _cache.pop(oldest, None)
    return Loaded(markets=list(markets), pages=pages, requests=requests, cache="miss")
