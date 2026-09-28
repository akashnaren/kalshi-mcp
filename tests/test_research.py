"""Research fields sit on find_best_bets rows without changing the rank score."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from kalshi_readonly.recommend import FIND_BEST_TOOL, find_best_bets
from kalshi_readonly.routine import FE_ROUTINE, fe_routine
from kalshi_readonly.score import edge_net_cents, fee_dome_cents, flb_band, taker_longshot_allowed

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
    assert row["side_exec"] == "maker"
    assert row["SIDE_EXEC"] == "maker"
    assert row["days_to_res"] == "7.00"
    assert row["DAYS_TO_RES"] == "7.00"
    assert row["spread_cents"] == "1.00"
    assert row["depth_at_ask"] == "80.0000"
    assert row["fee_cents_est"] == "0"
    assert row["corr_group_hint"] == "city_weather_week"
    assert row["hold_to_res_default"] is False
    assert row["HOLD_TO_RES_DEFAULT"] is False
    assert row["volume_floor"] == "24h"
    assert "volume_lifetime" in row
    assert "akash names the trade" not in row["do_not_place"].lower()


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
    assert payload["day_spend_remaining"] is None
    assert payload["day_spend_todo"].startswith("TODO:")
    assert "invent" in payload["day_spend_todo"]
    text = (ROOT / "harness" / "fe-grok-bot-routine.md").read_text(encoding="utf-8")
    start = text.index("```\n") + len("```\n")
    end = text.index("\n```", start)
    assert text[start:end] == FE_ROUTINE


def test_find_best_bets_blurb_allows_finance_engineer_to_place() -> None:
    text = FIND_BEST_TOOL["description"]
    assert "akash names the trade" not in text.lower()
    assert "confirm true" in text.lower()
    assert "0.84" in FIND_BEST_TOOL["inputSchema"]["properties"]["max_price"]["description"]
    assert "probability points" in FIND_BEST_TOOL["inputSchema"]["properties"]["min_edge"]["description"].lower()


def test_taker_longshot_needs_the_flag_the_edge_and_a_two_dollar_stake(monkeypatch) -> None:
    assert taker_longshot_allowed(allow_longshot=True, edge=Decimal("0.08")) is True
    assert taker_longshot_allowed(allow_longshot=True, edge=Decimal("0.079")) is False
    assert taker_longshot_allowed(allow_longshot=False, edge=Decimal("0.50")) is False

    def fake_public_get(path: str, query=None):
        return {
            "markets": [_market("LOTTO", yes_ask_dollars="0.0800", yes_bid_dollars="0")],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    belief = {"ticker": "LOTTO", "side": "yes", "confidence": 0.70, "evidence": "named tail"}
    blocked = find_best_bets({"beliefs": [belief]}, now=NOW)
    assert blocked["recommendations"] == []
    assert blocked["counts"]["skipped_flb"] == 1

    kept = find_best_bets({"beliefs": [{**belief, "allow_longshot": True}]}, now=NOW)
    row = kept["recommendations"][0]
    assert row["flb_band"] == "<10¢"
    assert row["side_exec"] == "taker"
    assert row["stake_mode"] == "fixed_2"
    assert Decimal(row["assumed_edge"]) >= Decimal("0.08")
    assert Decimal(row["suggested_max_dollars_risked"]) <= Decimal("2")
    assert row["suggested_contracts"] == 25


def test_max_price_default_is_documented_as_the_hard_ceiling() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "| `max_price` | 0.84 | 0.05 to 0.84 |" in readme
    assert "previous default of 0.50" in readme


def test_lifetime_volume_passes_when_the_24h_proxy_does_not(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        return {
            "markets": [
                _market("SEASONED", volume_24h_fp="5.00", volume_fp="1500.00"),
                _market("QUIET", volume_24h_fp="5.00", volume_fp="40.00"),
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "SEASONED", "side": "yes", "confidence": 0.70, "evidence": "lifetime book"},
                {"ticker": "QUIET", "side": "yes", "confidence": 0.70, "evidence": "too thin"},
            ],
            "limit": 5,
        },
        now=NOW,
    )
    assert [row["ticker"] for row in out["recommendations"]] == ["SEASONED"]
    row = out["recommendations"][0]
    assert row["volume_floor"] == "lifetime"
    assert row["volume_lifetime"] == "1500.0000"
    assert row["volume_24h"] == "5.0000"
    assert out["counts"]["skipped_liquidity"] == 1


def test_maker_sorts_ahead_of_an_equal_score_taker(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        return {
            "markets": [
                _market("A-TAKE", yes_bid_dollars="0"),
                _market("Z-MAKE", yes_bid_dollars="0.1400"),
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "A-TAKE", "side": "yes", "confidence": 0.70, "evidence": "cross the ask"},
                {"ticker": "Z-MAKE", "side": "yes", "confidence": 0.70, "evidence": "rest below the ask"},
            ]
        },
        now=NOW,
    )
    rows = out["recommendations"]
    assert [row["ticker"] for row in rows] == ["Z-MAKE", "A-TAKE"]
    assert rows[0]["side_exec"] == "maker"
    assert rows[1]["side_exec"] == "taker"
    assert rows[0]["score"] == rows[1]["score"]
