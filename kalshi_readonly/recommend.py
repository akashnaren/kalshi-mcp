"""Read-only bet ranking. Does not place, cancel, amend, or decrease."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from kalshi_readonly.guard import safe_mode_enabled
from kalshi_readonly.markets import load_markets
from kalshi_readonly.score import (
    DO_NOT_PLACE,
    filters_payload,
    parse_beliefs,
    parse_options,
    parse_time,
    rank_markets,
    research_queue,
)

__all__ = ["DO_NOT_PLACE", "FIND_BEST_TOOL", "find_best_bets", "main", "recommend_from_records"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _drop_empty(row: dict) -> dict:
    return {key: value for key, value in row.items() if value is not None}


def _envelope(
    *,
    recommendations: list[dict],
    research: list[dict],
    counts: dict,
    missing: list[str],
    options,
    source: str,
    cache: str,
    pages: int,
    requests: int,
) -> dict:
    return {
        "places_orders": False,
        "do_not_place": DO_NOT_PLACE,
        "safe_mode": safe_mode_enabled(),
        "source": source,
        "cache": cache,
        "pages_fetched": pages,
        "requests": requests,
        "scanned": counts.get("scanned", 0),
        "filters": filters_payload(options),
        "recommendations": [_drop_empty(row) for row in recommendations],
        "research_queue": [_drop_empty(row) for row in research],
        "missing": missing,
        "counts": counts,
    }


def find_best_bets(args: dict | None = None, *, now: datetime | None = None) -> dict:
    """Rank beliefs, or return a research queue when no confidence was supplied.

    `now` is for tests. Tool arguments cannot set the clock.
    """
    args = dict(args or {})
    args.pop("now", None)
    args.pop("markets", None)
    moment = now or utcnow()
    if moment.tzinfo is None:
        raise RuntimeError("clock must be timezone-aware")
    options = parse_options(args)
    beliefs = parse_beliefs(args.get("beliefs"))
    if beliefs:
        tickers = sorted({item["ticker"] for item in beliefs})
        loaded = load_markets(tickers=tickers, max_pages=1, page_size=200, scan=False)
        ranked, counts, missing = rank_markets(loaded.markets, beliefs, options, moment)
        research: list[dict] = []
        source = "beliefs"
    else:
        loaded = load_markets(
            tickers=None,
            max_pages=options.max_pages,
            page_size=options.page_size,
            scan=True,
        )
        research, counts = research_queue(loaded.markets, options, moment)
        ranked = []
        missing = []
        source = "scan"
    return _envelope(
        recommendations=ranked,
        research=research,
        counts=counts,
        missing=missing,
        options=options,
        source=source,
        cache=loaded.cache,
        pages=loaded.pages,
        requests=loaded.requests,
    )


def recommend_from_records(payload: dict) -> dict:
    """Score a fixture. Does not call Kalshi."""
    if not isinstance(payload, dict):
        raise RuntimeError("fixture must be an object")
    moment = parse_time(payload.get("now"))
    if moment is None:
        raise RuntimeError("fixture now must be an ISO timestamp")
    markets = payload.get("markets")
    if not isinstance(markets, list):
        raise RuntimeError("fixture markets must be a list")
    args = {key: value for key, value in payload.items() if key not in {"markets", "now"}}
    options = parse_options(args)
    beliefs = parse_beliefs(args.get("beliefs"))
    if beliefs:
        ranked, counts, missing = rank_markets(markets, beliefs, options, moment)
        research: list[dict] = []
    else:
        research, counts = research_queue(markets, options, moment)
        ranked = []
        missing = []
    return _envelope(
        recommendations=ranked,
        research=research,
        counts=counts,
        missing=missing,
        options=options,
        source="fixture",
        cache="fixture",
        pages=0,
        requests=0,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dry-run Kalshi rankings from a fixture file. Does not place orders and does not call Kalshi."
    )
    parser.add_argument("--fixture", required=True, help="JSON file with now, markets, and optional beliefs")
    args = parser.parse_args(argv)
    try:
        payload = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        result = recommend_from_records(payload)
    except (OSError, json.JSONDecodeError, RuntimeError) as exc:
        text = str(exc)
        if "PRIVATE KEY" in text or "-----BEGIN" in text:
            text = "fixture failed"
        print(text, file=sys.stderr)
        return 2
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


FIND_BEST_TOOL = {
    "name": "find_best_bets",
    "description": (
        "Read-only ranking of open Kalshi markets. "
        "Score is estimated_confidence times payout_ratio divided by stake_needed. "
        "Each row also reports edge_net_cents after the Kalshi fee dome, flb_band, kelly_frac 0.25, and stake_mode. "
        "Prefers a small stake and a high payout when confidence in that yes or no side is high. "
        "Pass beliefs with ticker, side (yes or no), confidence, and optional evidence. "
        "Without beliefs, returns a short research queue and no recommendations. "
        "Does not place, cancel, amend, or decrease. "
        "do not place until Akash names the trade."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "beliefs": {
                "type": "array",
                "description": "At most 20. side is yes or no, not bid or ask. confidence is 0 to 1.",
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "side": {"type": "string", "enum": ["yes", "no"]},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["ticker", "side", "confidence"],
                },
            },
            "max_pages": {"type": "integer", "description": "Scan pages when beliefs are omitted. 1 to 4. Default 2."},
            "page_size": {"type": "integer", "description": "Markets per scan page. 1 to 200. Default 100."},
            "min_volume": {"type": "number", "description": "Minimum 24h volume in contracts. Default 20."},
            "min_ask_size": {"type": "number", "description": "Minimum displayed size at the ask. Default 1."},
            "min_confidence": {"type": "number", "description": "Floor is 0.50. Default 0.55."},
            "min_edge": {"type": "number", "description": "Confidence minus the ask. Default 0.08."},
            "min_hours_to_expiry": {"type": "number", "description": "Default 2."},
            "max_hours_to_expiry": {"type": "number", "description": "Default 1440 (60 days)."},
            "max_price": {"type": "number", "description": "Most you will pay per contract. Default 0.50. Hard ceiling 0.84."},
            "max_risk_dollars": {"type": "number", "description": "Cap on suggested dollars at risk. 1 to 25. Default 5."},
            "limit": {"type": "integer", "description": "How many rows to return. 1 to 10. Default 5."},
        },
    },
}


if __name__ == "__main__":
    raise SystemExit(main())
