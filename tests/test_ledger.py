"""Decision ledger, place echo counts, find_best_bets gates, and liquid list_markets."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kalshi_readonly.ledger import append_decision, summarize_decisions
from kalshi_readonly.recommend import find_best_bets
from kalshi_readonly.tools import list_markets

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "tests" / "fixtures" / "decisions.seed.jsonl"
NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)

_PLACE = {
    "id": "20260930-180000-KXHIGHCHI-26SEP30-yes-01",
    "as_of": "2026-09-30T18:00:00-07:00",
    "action": "place",
    "ticker": "KXHIGHCHI-26SEP30",
    "side": "yes",
    "corr_group": "city_weather_20260930",
    "category": "weather",
    "belief_conf": 0.7,
    "model_sources": ["GFS"],
    "evidence_summary": "model high is above the strike",
    "edge_net_cents": 4.5,
    "fee_cents_est": 1,
    "flb_band": "25–75¢",
    "days_to_res": 1,
    "maker_flag": True,
    "side_exec": "maker",
    "stake_dollars": 2,
    "count": 4,
    "price": 0.5,
    "gates_passed": ["beliefs_present", "edge_net"],
    "gates_failed": [],
    "failed_gate": None,
    "logic_path": ["beliefs_present", "edge_net"],
    "rank_source": "find_best_bets",
    "order_id": "ord-1",
    "confirm": True,
}


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KALSHI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("KALSHI_LEDGER_PATH", raising=False)


def _seed_lines() -> list[dict]:
    return [json.loads(line) for line in SEED.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_seed_appends_and_summary_uses_real_counts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _state(monkeypatch, tmp_path)
    empty = summarize_decisions({"write_stats": False})
    assert empty["lines"] == 0
    assert empty["ltd"]["n_places"] == 0
    assert empty["ltd"]["n_skips"] == 0
    assert empty["ltd"]["n_resolved"] == 0
    assert empty["ltd"]["hit_rate"] is None
    assert empty["ltd"]["roi_after_fees"] is None
    assert empty["ltd"]["pnl_dollars"] is None
    assert empty["ltd"]["brier"] is None
    assert empty["ltd"]["claim_level"] == "counts_and_pnl_only"
    assert "external" in empty["ltd"]["external_priors"]
    assert empty["ltd"]["max_concurrent_pct"] is None

    first = None
    for record in _seed_lines():
        written = append_decision({"record": record})
        first = first or written
    assert first is not None
    path = Path(first["path"])
    before = path.read_text(encoding="utf-8")
    summary = summarize_decisions({"week": "2026-W40"})
    assert summary["week"]["n_places"] == 0
    assert summary["week"]["n_skips"] == 3
    assert summary["week"]["n_resolved"] == 0
    assert summary["week"]["hit_rate"] is None
    assert summary["ltd"]["n_skips"] == 3
    assert summary["ltd"]["skip_reasons"] == {"edge_below_floor": 1, "price_band": 2}
    assert summary["ltd"]["n_resolved"] == 0
    assert "stats-weekly-2026-W40.json" in summary["stats_weekly_path"]
    weekly = json.loads(Path(summary["stats_weekly_path"]).read_text(encoding="utf-8"))
    assert weekly["week"]["n_skips"] == 3
    assert path.read_text(encoding="utf-8") == before

    with pytest.raises(RuntimeError, match="id already exists"):
        append_decision({"record": _seed_lines()[0]})
    assert path.read_text(encoding="utf-8") == before

    broken = dict(_seed_lines()[0])
    broken["id"] = "20260930-1711-KXOTHER-no-01"
    del broken["ticker"]
    with pytest.raises(RuntimeError, match="ticker"):
        append_decision({"record": broken})
    assert path.read_text(encoding="utf-8") == before


def test_place_and_outcome_drive_roi_without_inventing_n(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _state(monkeypatch, tmp_path)
    append_decision({"record": _PLACE})
    outcome = {
        "id": "20261005-120000-KXHIGHCHI-26SEP30-yes-out",
        "as_of": "2026-10-05T12:00:00-07:00",
        "action": "outcome",
        "parent_id": _PLACE["id"],
        "settled": True,
        "result": "win",
        "pnl_dollars": 1,
        "held_to_res": True,
        "exit_reason": "settlement",
        "resolved_at": "2026-10-05T12:00:00-07:00",
    }
    append_decision({"record": outcome})
    place_week = summarize_decisions({"week": "2026-W40", "write_stats": False})
    resolve_week = summarize_decisions({"week": "2026-W41", "write_stats": False})
    assert place_week["week"]["n_places"] == 1
    assert place_week["week"]["n_resolved"] == 0
    assert resolve_week["week"]["n_places"] == 0
    assert resolve_week["week"]["n_resolved"] == 1
    ltd = resolve_week["ltd"]
    assert ltd["n_places"] == 1
    assert ltd["n_resolved"] == 1
    assert ltd["n_wins"] == 1
    assert ltd["hit_rate"] == "1.0000"
    assert ltd["roi_after_fees"] == "0.5000"
    assert ltd["pnl_dollars"] == "1.00"
    assert ltd["stake_dollars"] == "2.00"
    assert ltd["by_flb_band"]["25–75¢"]["n_resolved"] == 1
    assert ltd["by_maker"]["maker"]["n_resolved"] == 1
    assert ltd["by_category"]["weather"]["roi_after_fees"] == "0.5000"
    assert ltd["by_days_to_res"]["<=3"]["n_resolved"] == 1
    assert ltd["edge_net_realized_vs_predicted"][0]["edge_net_cents_predicted"] == "4.5"
    assert ltd["edge_net_realized_vs_predicted"][0]["pnl_per_stake"] == "0.5000"
    assert ltd["brier"] is None
    assert ltd["brier_reason"] == "n_binary<20"
    assert ltd["claim_level"] == "counts_and_pnl_only"
    assert ltd["external_priors"]
    assert ltd["max_concurrent_pct"] is None


def test_brier_waits_for_twenty_binary_outcomes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _state(monkeypatch, tmp_path)
    for index in range(20):
        place = dict(_PLACE)
        place["id"] = f"20260930-180100-KXHIGHCHI-26SEP30-yes-{index + 1:02d}"
        place["order_id"] = f"ord-{index}"
        append_decision({"record": place})
        append_decision(
            {
                "record": {
                    "id": f"20261002-120100-KXHIGHCHI-26SEP30-yes-{index + 1:02d}",
                    "as_of": "2026-10-02T12:01:00-07:00",
                    "action": "outcome",
                    "parent_id": place["id"],
                    "settled": True,
                    "result": "win" if index % 2 == 0 else "loss",
                    "pnl_dollars": 1 if index % 2 == 0 else -1,
                    "held_to_res": True,
                    "exit_reason": "settlement",
                    "resolved_at": "2026-10-02T12:01:00-07:00",
                }
            }
        )
    ltd = summarize_decisions({"write_stats": False})["ltd"]
    assert ltd["n_resolved"] == 20
    assert ltd["n_binary"] == 20
    assert ltd["n_wins"] == 10
    assert ltd["hit_rate"] == "0.5000"
    assert ltd["brier"] is not None
    assert ltd["log_score"] is not None
    assert ltd["claim_level"] == "directional_wide_uncertainty"
    assert ltd["external_priors"]


def _market(ticker: str, **overrides) -> dict:
    row = {
        "ticker": ticker,
        "event_ticker": f"E-{ticker}",
        "title": ticker,
        "status": "active",
        "market_type": "binary",
        "yes_ask_dollars": "0.1500",
        "yes_bid_dollars": "0.1400",
        "no_ask_dollars": "0.8600",
        "yes_ask_size_fp": "80.00",
        "volume_24h_fp": "500.00",
        "close_time": "2026-10-05T15:00:00Z",
        "notional_value_dollars": "1.0000",
    }
    row.update(overrides)
    return row


def test_find_best_bets_rows_carry_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    markets = [
        _market("CHEAP-HIGH"),
        _market("PRICE-HIGH", yes_ask_dollars="0.9200", yes_bid_dollars="0.9000", no_ask_dollars="0.1000"),
    ]

    def fake_public_get(path: str, query=None):
        assert path == "/markets"
        return {"markets": markets, "cursor": ""}

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "CHEAP-HIGH", "side": "yes", "confidence": 0.70, "evidence": "named"},
                {"ticker": "PRICE-HIGH", "side": "yes", "confidence": 0.95, "evidence": "too expensive"},
            ]
        },
        now=NOW,
    )
    row = out["recommendations"][0]
    assert row["ticker"] == "CHEAP-HIGH"
    assert row["gates_failed"] == []
    assert "beliefs_present" in row["gates_passed"]
    assert "edge_net" in row["gates_passed"]
    assert "flb_band" in row["gates_passed"]
    skipped = out["skipped"]
    assert len(skipped) == 1
    assert skipped[0]["ticker"] == "PRICE-HIGH"
    assert skipped[0]["failed_gate"] == "price_band"
    assert skipped[0]["gates_failed"] == ["price_band"]
    assert "beliefs_present" in skipped[0]["gates_passed"]
    assert "exchange_trading_active" in skipped[0]["gates_passed"]
    assert skipped[0]["flb_band"] == "≥90¢"
    assert out["counts"]["skipped_price"] == 1


def test_list_markets_skips_mve_null_books_and_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        {
            "markets": [
                {
                    "ticker": "KXMVECROSSCATEGORY-1",
                    "title": "combo",
                    "status": "open",
                    "yes_ask": None,
                    "yes_bid": None,
                    "volume": None,
                }
            ],
            "cursor": "page-2",
        },
        {
            "markets": [
                {
                    "ticker": "KXHIGHCHI-26OCT01",
                    "title": "Chicago high",
                    "status": "open",
                    "yes_ask": 42,
                    "yes_bid": 40,
                    "volume": 100,
                    "series_ticker": "KXHIGH",
                    "close_time": "2026-10-01T12:00:00Z",
                },
                {
                    "ticker": "KXNFLGAME-26OCT01",
                    "title": "NFL",
                    "status": "open",
                    "yes_ask": 55,
                    "yes_bid": 54,
                    "volume": 20,
                    "series_ticker": "KXNFL",
                },
            ],
            "cursor": "page-3",
        },
    ]
    seen: list[dict] = []

    def fake_public_get(path: str, query=None):
        assert path == "/markets"
        seen.append(dict(query or {}))
        return pages[len(seen) - 1]

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    out = list_markets({"category": "weather"})
    assert [row["ticker"] for row in out["markets"]] == ["KXHIGHCHI-26OCT01"]
    assert out["pages_fetched"] == 2
    assert out["dropped_mve"] == 1
    assert out["cursor"] == "page-3"
    assert seen[0]["mve_filter"] == "exclude"
    assert seen[0]["status"] == "open"
    assert seen[0]["limit"] == 25
    assert seen[1]["cursor"] == "page-2"
    assert "category" not in seen[0]

    seen.clear()

    def one_page(path: str, query=None):
        seen.append(dict(query or {}))
        return {
            "markets": [
                {"ticker": "KXHIGHCHI-26OCT01", "yes_ask": None, "yes_bid": None, "volume": None, "series_ticker": "KXHIGH"},
                {"ticker": "KXFEDDECISION-26OCT", "yes_ask": 30, "yes_bid": 28, "volume": 10, "series_ticker": "KXFED"},
            ],
            "cursor": "next",
        }

    monkeypatch.setattr("kalshi_readonly.tools.public_get", one_page)
    kept = list_markets({"series_ticker": "KXFED", "liquid": False, "limit": 10})
    assert seen[0]["series_ticker"] == "KXFED"
    assert [row["ticker"] for row in kept["markets"]] == ["KXHIGHCHI-26OCT01", "KXFEDDECISION-26OCT"]
    assert kept["dropped_null_book"] == 0
