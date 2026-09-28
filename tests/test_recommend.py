"""find_best_bets ranks a small stake with high payout ahead of a near-certain price."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kalshi_readonly.guard import safe_mode_enabled
from kalshi_readonly.recommend import DO_NOT_PLACE, find_best_bets, recommend_from_records
from kalshi_readonly.recommend import main as recommend_main
from kalshi_readonly.score import score_formula
from kalshi_readonly.tools import registered_tools

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "recommend_scan.json"
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)


def _market(
    ticker: str,
    *,
    yes_ask: str = "0.1500",
    no_ask: str = "0.8600",
    yes_size: str = "80.00",
    no_size: str = "80.00",
    volume: str = "500.00",
    close: str = "2026-10-05T15:00:00Z",
    status: str = "active",
    title: str | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": f"E-{ticker}",
        "title": title or ticker,
        "status": status,
        "market_type": "binary",
        "yes_ask_dollars": yes_ask,
        "no_ask_dollars": no_ask,
        "yes_ask_size_fp": yes_size,
        "no_ask_size_fp": no_size,
        "volume_24h_fp": volume,
        "close_time": close,
        "notional_value_dollars": "1.0000",
    }


CHEAP = _market("CHEAP-HIGH", yes_ask="0.1500", title="Cheap high payout")
HEAVY = _market("HEAVY-NEAR", yes_ask="0.8200", no_ask="0.1900", title="Near certain")
MARKETS = [CHEAP, HEAVY]


def _beliefs() -> list[dict]:
    return [
        {"ticker": "HEAVY-NEAR", "side": "yes", "confidence": 0.93, "evidence": "priced as near certain"},
        {"ticker": "CHEAP-HIGH", "side": "yes", "confidence": 0.70, "evidence": "three reports agree"},
    ]


@pytest.fixture(autouse=True)
def _clear_caches():
    from kalshi_readonly.markets import clear_market_cache

    clear_market_cache()
    yield
    clear_market_cache()


def _patch_markets(monkeypatch: pytest.MonkeyPatch, payload: dict | None = None, *, cursor: str = ""):
    calls: list[dict] = []

    def fake_public_get(path: str, query: dict | None = None):
        assert path == "/markets"
        calls.append(dict(query or {}))
        return {"markets": list(payload if payload is not None else MARKETS), "cursor": cursor}

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    return calls


def test_find_best_bets_ranks_cheap_high_confidence_over_near_certain(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_markets(monkeypatch, cursor="do-not-follow")

    def boom(*_args, **_kwargs):
        raise AssertionError("mutation was called")

    monkeypatch.setattr("kalshi_readonly.http.auth_call", boom)
    out = find_best_bets(
        {"beliefs": _beliefs(), "max_price": "0.84", "max_risk_dollars": "5"},
        now=NOW,
    )

    assert out["places_orders"] is False
    assert out["do_not_place"] == DO_NOT_PLACE
    assert [row["ticker"] for row in out["recommendations"]] == ["CHEAP-HIGH", "HEAVY-NEAR"]
    assert len(calls) == 1
    assert calls[0]["status"] == "open"
    assert set(calls[0]["tickers"].split(",")) == {"CHEAP-HIGH", "HEAVY-NEAR"}
    assert "cursor" not in calls[0]
    assert out["requests"] == 1
    assert out["cache"] == "miss"

    cheap = out["recommendations"][0]
    stake = Decimal("0.1500")
    confidence = Decimal("0.70")
    payout_ratio = (Decimal("1") - stake) / stake
    expected = score_formula(confidence, payout_ratio, stake)
    assert cheap["side"] == "yes"
    assert Decimal(cheap["stake_needed"]) == stake
    assert Decimal(cheap["estimated_confidence"]) == confidence
    assert Decimal(cheap["payout_ratio"]) == payout_ratio.quantize(Decimal("0.0001"))
    assert Decimal(cheap["score"]) == expected
    assert Decimal(cheap["assumed_edge"]) == confidence - stake
    assert Decimal(cheap["suggested_max_dollars_risked"]) == Decimal("4.95")
    assert cheap["suggested_contracts"] == 33
    assert cheap["do_not_place"] == DO_NOT_PLACE
    assert "three reports agree" in cheap["rationale"]
    assert Decimal(cheap["score"]) > Decimal(out["recommendations"][1]["score"])

    narrow = find_best_bets({"beliefs": _beliefs()}, now=NOW)
    assert [row["ticker"] for row in narrow["recommendations"]] == ["CHEAP-HIGH"]
    assert narrow["cache"] == "hit"
    assert narrow["requests"] == 0
    assert len(calls) == 1


def test_find_best_bets_prices_no_off_the_no_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    market = _market("NOCHEAP", yes_ask="0.8000", no_ask="0.1800", yes_size="10.00", no_size="40.00")
    calls = _patch_markets(monkeypatch, [market])
    out = find_best_bets(
        {"beliefs": [{"ticker": "NOCHEAP", "side": "no", "confidence": 0.62, "evidence": "no side"}]},
        now=NOW,
    )
    assert len(calls) == 1
    row = out["recommendations"][0]
    assert row["side"] == "no"
    assert Decimal(row["stake_needed"]) == Decimal("0.1800")
    assert row["do_not_place"] == DO_NOT_PLACE

    calls.clear()
    with pytest.raises(RuntimeError, match="side must be yes or no"):
        find_best_bets(
            {"beliefs": [{"ticker": "NOCHEAP", "side": "bid", "confidence": 0.8}]},
            now=NOW,
        )
    assert calls == []


def test_find_best_bets_reads_the_clock_when_now_is_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_markets(monkeypatch, [])
    out = find_best_bets({"max_pages": 1, "page_size": 1})
    assert len(calls) == 1
    assert out["places_orders"] is False
    assert out["do_not_place"] == DO_NOT_PLACE
    assert out["recommendations"] == []


def test_find_best_bets_is_a_read_while_safe_mode_defaults_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    assert safe_mode_enabled() is True
    names = [tool["name"] for tool in registered_tools()]
    assert "find_best_bets" in names
    assert "place_order" not in names
    tool = next(item for item in registered_tools() if item["name"] == "find_best_bets")
    assert "does not place" in tool["description"].lower()


def test_find_best_bets_stops_at_max_pages_and_reuses_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []

    def fake_public_get(path: str, query: dict | None = None):
        calls.append(dict(query or {}))
        ticker = f"M{len(calls)}"
        return {
            "markets": [
                _market(ticker, yes_ask="0.2000", close="2026-10-02T15:00:00Z", title=ticker)
            ],
            "cursor": "more",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    first = find_best_bets({"max_pages": 2, "page_size": 50}, now=NOW)
    assert len(calls) == 2
    assert calls[1]["cursor"] == "more"
    assert first["cache"] == "miss"
    assert first["pages_fetched"] == 2
    assert first["recommendations"] == []
    assert [row["ticker"] for row in first["research_queue"]] == ["M1", "M2"]
    assert first["research_queue"][0]["note"] == "no confidence supplied; not a recommendation"

    second = find_best_bets({"max_pages": 2, "page_size": 50}, now=NOW)
    assert len(calls) == 2
    assert second["cache"] == "hit"
    assert second["requests"] == 0
    assert [row["ticker"] for row in second["research_queue"]] == ["M1", "M2"]


def test_find_best_bets_filters_liquidity_expiry_and_low_confidence(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = [
        _market("OK", yes_ask="0.2000"),
        _market("THIN", yes_ask="0.1000", volume="1.00", yes_size="0.00"),
        _market("SOON", yes_ask="0.1000", close="2026-09-28T15:30:00Z"),
        _market("FAR", yes_ask="0.1000", close="2028-09-28T15:00:00Z"),
        _market("DUST", yes_ask="0.0100"),
        _market("CLOSED", yes_ask="0.1000", status="finalized"),
        _market("LOW", yes_ask="0.2000"),
    ]
    _patch_markets(monkeypatch, markets)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "OK", "side": "yes", "confidence": 0.66, "evidence": "enough"},
                {"ticker": "THIN", "side": "yes", "confidence": 0.80},
                {"ticker": "SOON", "side": "yes", "confidence": 0.80},
                {"ticker": "FAR", "side": "yes", "confidence": 0.80},
                {"ticker": "DUST", "side": "yes", "confidence": 0.80},
                {"ticker": "CLOSED", "side": "yes", "confidence": 0.80},
                {"ticker": "LOW", "side": "yes", "confidence": 0.40},
                {"ticker": "GONE", "side": "no", "confidence": 0.70},
            ]
        },
        now=NOW,
    )
    assert [row["ticker"] for row in out["recommendations"]] == ["OK"]
    assert out["missing"] == ["GONE"]
    assert out["counts"]["skipped_liquidity"] == 1
    assert out["counts"]["skipped_expiry"] == 2
    assert out["counts"]["skipped_price"] == 1
    assert out["counts"]["skipped_market"] == 1
    assert out["counts"]["skipped_confidence"] == 1
    assert out["counts"]["missing"] == 1


def test_recommend_cli_dry_run_fixture_does_not_call_http(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("http was called")

    monkeypatch.setattr("kalshi_readonly.markets.public_get", boom)
    monkeypatch.setattr("kalshi_readonly.http.auth_call", boom)
    assert recommend_main(["--fixture", str(FIXTURE)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["places_orders"] is False
    assert out["do_not_place"] == DO_NOT_PLACE
    assert out["cache"] == "fixture"
    assert out["requests"] == 0
    assert [row["ticker"] for row in out["recommendations"]] == ["CHEAP-HIGH", "HEAVY-NEAR"]
    assert out["recommendations"][0]["do_not_place"] == DO_NOT_PLACE
    assert out["missing"] == ["GONE"]
    assert out["counts"]["skipped_liquidity"] == 1
    assert out["counts"]["skipped_expiry"] == 1
    assert out["counts"]["skipped_confidence"] == 1
    assert Decimal(out["recommendations"][0]["score"]) > Decimal(out["recommendations"][1]["score"])
    assert "HEAVY-NEAR" != out["recommendations"][0]["ticker"]
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    again = recommend_from_records(payload)
    assert again["recommendations"][0]["ticker"] == "CHEAP-HIGH"


def test_dispatch_lists_reads_and_reuses_one_process_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    from kalshi_readonly.dispatch import handle

    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    monkeypatch.setattr("kalshi_readonly.recommend.utcnow", lambda: NOW)
    listed = handle({"id": 1, "op": "list"})
    names = [tool["name"] for tool in listed["tools"]]
    assert "find_best_bets" in names
    assert "place_order" not in names

    calls = _patch_markets(monkeypatch)
    first = handle(
        {
            "id": 2,
            "op": "call",
            "name": "find_best_bets",
            "arguments": {"beliefs": _beliefs(), "max_price": "0.84"},
        }
    )
    second = handle(
        {
            "id": 3,
            "op": "call",
            "name": "find_best_bets",
            "arguments": {"beliefs": _beliefs(), "max_price": "0.84"},
        }
    )
    assert first["ok"] is True and second["ok"] is True
    assert first["result"]["cache"] == "miss"
    assert second["result"]["cache"] == "hit"
    assert len(calls) == 1
    assert first["result"]["recommendations"][0]["ticker"] == "CHEAP-HIGH"

    proc = subprocess.run(
        [sys.executable, "-m", "kalshi_readonly.dispatch"],
        input=json.dumps({"id": 7, "op": "list"}) + "\n",
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0
    line = proc.stdout.strip().splitlines()
    assert len(line) == 1
    payload = json.loads(line[0])
    assert payload["id"] == 7
    assert payload["ok"] is True
    assert "find_best_bets" in [tool["name"] for tool in payload["tools"]]
    assert "PRIVATE KEY" not in proc.stdout
