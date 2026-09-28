"""Research fields sit on find_best_bets rows without changing the rank score."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kalshi_readonly.recommend import find_best_bets
from kalshi_readonly.routine import FE_ROUTINE, fe_routine
from kalshi_readonly.score import edge_net_cents, fee_dome_cents, flb_band

ROOT = Path(__file__).resolve().parents[1]

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)


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


def test_fee_dome_and_band() -> None:
    assert fee_dome_cents(Decimal("0.50")) == Decimal(2)
    assert fee_dome_cents(Decimal("0.15")) == Decimal(1)
    assert flb_band(Decimal("0.08")) == "<10¢"
    assert flb_band(Decimal("0.15")) == "10–25¢"
    net = edge_net_cents(Decimal("0.70"), Decimal("0.15"), Decimal("0.14"))
    assert net > Decimal(8)


def test_ranked_row_carries_edge_net_and_quarter_kelly(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        assert path == "/markets"
        return {"markets": [_market("CHEAP-HIGH")], "cursor": ""}

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [{
                "ticker": "CHEAP-HIGH",
                "side": "yes",
                "confidence": 0.70,
                "evidence": "three reports agree",
                "key": "nhc_cone_includes_city",
                "category_tag": "Weather",
                "corr_group": "city_weather_week",
                "model_sources": "GFS ensemble versus the station",
            }],
            "max_price": "0.84",
        },
        now=NOW,
    )
    row = out["recommendations"][0]
    assert out["places_orders"] is False
    assert Decimal(row["score"]) > 0
    assert row["flb_band"] == "10–25¢"
    assert row["stake_mode"] == "fixed_2"
    assert row["kelly_frac"] == "0.25"
    assert Decimal(row["edge_net_cents"]) > 0
    assert row["key"] == "nhc_cone_includes_city"
    assert row["corr_group"] == "city_weather_week"
    assert "0.07" in row["fee_dome"]


def test_dominated_yes_is_dropped(monkeypatch) -> None:
    markets = [
        _market(
            "YES-HI",
            event_ticker="SPLIT-1",
            yes_bid_dollars="0.5800",
            yes_ask_dollars="0.6200",
        ),
        _market(
            "YES-LO",
            event_ticker="SPLIT-1",
            yes_bid_dollars="0.5300",
            yes_ask_dollars="0.5700",
        ),
    ]

    def fake_public_get(path: str, query=None):
        return {"markets": markets, "cursor": ""}

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "YES-HI", "side": "yes", "confidence": 0.80, "evidence": "high strike still cheap"},
                {"ticker": "YES-LO", "side": "yes", "confidence": 0.75, "evidence": "low strike is the cheap leg"},
            ],
            "max_price": "0.84",
            "limit": 10,
        },
        now=NOW,
    )
    assert [row["ticker"] for row in out["recommendations"]] == ["YES-LO"]
    assert out["recommendations"][0]["yes_no_replicate"] == "cheaper_leg"
    assert out["counts"]["skipped_yes_replicate"] == 1


def test_fee_blind_text_is_not_a_recommendation(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        return {"markets": [_market("BLIND")], "cursor": ""}

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [{
                "ticker": "BLIND",
                "side": "yes",
                "confidence": 0.8,
                "evidence": "fee-blind backtest of the mid",
            }]
        },
        now=NOW,
    )
    assert out["recommendations"] == []
    assert out["counts"]["skipped_fee_blind"] == 1


def test_routine_names_the_daily_and_eod_hook() -> None:
    payload = fe_routine({})
    assert payload["places_orders"] is False
    assert payload["cadence"] == ["DAILY", "EOD"]
    assert payload["routine"] == FE_ROUTINE
    for phrase in (
        "edge_net",
        "fee_dome",
        "flb_band",
        "Kelly",
        "find_best_bets",
        "KALSHI_SAFE_MODE=0",
        "confirm:true",
        "$2",
        "$15",
        "15%",
        "corr_group",
        "hold to settlement",
        "DAILY",
        "EOD",
        "withdraw",
        "rate_limited",
        "tools/list",
        "npm run build",
        "KALSHI_SAFE_MODE=1",
        "no separate Kalshi role harness",
    ):
        assert phrase in FE_ROUTINE
    text = (ROOT / "harness" / "fe-grok-bot-routine.md").read_text(encoding="utf-8")
    start = text.index("```\n") + len("```\n")
    end = text.index("\n```", start)
    assert text[start:end] == FE_ROUTINE
