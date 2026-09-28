"""Research fields sit on find_best_bets rows without changing the rank score."""

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from kalshi_readonly.recommend import FIND_BEST_TOOL, find_best_bets
from kalshi_readonly.routine import FE_ROUTINE, fe_routine
from kalshi_readonly.score import (
    depth_haircut_cents,
    edge_net_cents,
    fee_dome_cents,
    flb_band,
    hold_to_res_default,
    maker_fee_cents,
    parse_beliefs,
    series_fees,
    taker_longshot_allowed,
)

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
    assert Decimal("0.07") * Decimal("0.25") * Decimal(100) == Decimal("1.75")
    assert fee_dome_cents(Decimal("0.50")) == Decimal(2)
    assert fee_dome_cents(Decimal("0.15")) == Decimal(1)
    assert flb_band(Decimal("0.08")) == "<10¢"
    assert flb_band(Decimal("0.15")) == "10–25¢"
    net = edge_net_cents(Decimal("0.70"), Decimal("0.15"), Decimal("0.14"))
    assert net > Decimal(8)
    assert fee_dome_cents(Decimal("0.50"), multiplier=Decimal(2)) == Decimal(4)
    assert maker_fee_cents(Decimal("0.50"), multiplier=None) == Decimal(0)
    assert maker_fee_cents(Decimal("0.50"), multiplier=Decimal(1)) == Decimal(1)
    assert depth_haircut_cents(Decimal("80")) == Decimal(0)
    assert depth_haircut_cents(Decimal("1")) == Decimal(2)
    deep = edge_net_cents(Decimal("0.70"), Decimal("0.15"), Decimal("0.14"), depth=Decimal("80"))
    thin = edge_net_cents(Decimal("0.70"), Decimal("0.15"), Decimal("0.14"), depth=Decimal("1"))
    assert deep - thin == Decimal("2.00")


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
    assert row["maker_flag"] is True
    assert row["days_to_res"] == "7.00"
    assert row["DAYS_TO_RES"] == "7.00"
    assert row["spread_cents"] == "1.00"
    assert row["depth_at_ask"] == "80.0000"
    assert row["fee_cents_est"] == "0"
    assert row["m_taker"] == "1"
    assert row["m_maker"] == "0"
    assert row["fee_m_source"] == "default"
    assert "take_profit" not in row
    assert "tp_pct" not in row
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
    assert rows[0]["maker_flag"] is True
    assert rows[1]["maker_flag"] is False
    assert rows[0]["spread_cents"] == "1.00"
    assert rows[0]["depth_at_ask"] == "80.0000"
    assert rows[0]["score"] == rows[1]["score"]


def test_series_multiplier_scales_taker_and_maker_fees(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        return {
            "markets": [
                _market("M-MAKE", fee_multiplier="2"),
                _market("M-TAKE", yes_bid_dollars="0", fee_multiplier="2"),
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "M-MAKE", "side": "yes", "confidence": 0.70, "evidence": "rest under a doubled series"},
                {"ticker": "M-TAKE", "side": "yes", "confidence": 0.70, "evidence": "take a doubled series"},
            ]
        },
        now=NOW,
    )
    by_ticker = {row["ticker"]: row for row in out["recommendations"]}
    assert by_ticker["M-MAKE"]["maker_flag"] is True
    assert by_ticker["M-MAKE"]["series_fee_multiplier"] == "2"
    assert by_ticker["M-MAKE"]["fee_m_source"] == "payload"
    assert by_ticker["M-MAKE"]["m_taker"] == "2"
    assert by_ticker["M-MAKE"]["m_maker"] == "2"
    assert by_ticker["M-MAKE"]["fee_cents_est"] == "1"
    assert by_ticker["M-TAKE"]["maker_flag"] is False
    assert by_ticker["M-TAKE"]["side_exec"] == "taker"
    assert by_ticker["M-TAKE"]["fee_cents_est"] == "2"
    assert by_ticker["M-TAKE"]["spread_cents"] is None
    assert Decimal(by_ticker["M-TAKE"]["depth_at_ask"]) >= Decimal("3")


def test_series_catalog_sets_split_maker_and_taker_m() -> None:
    nfl = series_fees({"ticker": "KXNFLGAME-25SEP28KC", "series_ticker": "KXNFL"})
    assert nfl == (Decimal(1), Decimal(1), "catalog")
    assert series_fees({"ticker": "KXNFLGAME-1", "series_ticker": "KXHIGH"})[2] == "catalog"
    assert series_fees({"ticker": "PLAIN", "series_ticker": "KXFEDDECISION"})[:2] == (Decimal(1), Decimal(1))
    assert series_fees({"ticker": "KXNBAGAME-1"}) == (Decimal(1), Decimal(1), "catalog")
    assert series_fees({"ticker": "KXMVE-1"}) == (Decimal(1), Decimal(2), "catalog")
    assert series_fees({"ticker": "KXHIGHNY-26SEP28"}) == (Decimal(1), Decimal(0), "inference")
    assert series_fees({"ticker": "KXBTCY-26"}) == (Decimal(0), Decimal(0), "catalog")
    assert series_fees({"ticker": "KXETHY-26"}) == (Decimal(0), Decimal(0), "catalog")
    assert series_fees({"ticker": "KXTEST"}) == (Decimal(1), Decimal(0), "default")
    assert series_fees({"ticker": "KXNFLGAME-1", "fee_multiplier": "2"}) == (Decimal(2), Decimal(2), "payload")
    assert hold_to_res_default(days=Decimal(7), net_cents=Decimal(1), fee_cents=Decimal(1)) is True
    assert hold_to_res_default(days=Decimal(7), net_cents=Decimal(2), fee_cents=Decimal(1)) is False
    assert hold_to_res_default(days=Decimal(8), net_cents=Decimal(1), fee_cents=Decimal(1)) is False
    assert hold_to_res_default(days=Decimal(7), net_cents=Decimal(100), fee_cents=Decimal(1)) is False


def test_catalog_fees_reach_ranked_rows_and_weather_gap_is_only_a_log(monkeypatch) -> None:
    def fake_public_get(path: str, query=None):
        return {
            "markets": [
                _market("KXNFLGAME-25SEP28KC"),
                _market("KXHIGHNY-26SEP28"),
                _market("KXBTCY-26", yes_bid_dollars="0"),
                _market("PLAIN-TAKE", yes_bid_dollars="0"),
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.markets.public_get", fake_public_get)
    out = find_best_bets(
        {
            "beliefs": [
                {"ticker": "KXNFLGAME-25SEP28KC", "side": "yes", "confidence": 0.70, "evidence": "listed nfl maker"},
                {
                    "ticker": "KXHIGHNY-26SEP28",
                    "side": "yes",
                    "confidence": 0.70,
                    "evidence": "weather gap is a log",
                    "wx_gap_pp": 12,
                },
                {"ticker": "KXBTCY-26", "side": "yes", "confidence": 0.70, "evidence": "crypto year end is free"},
                {"ticker": "PLAIN-TAKE", "side": "yes", "confidence": 0.70, "evidence": "unlisted taker still pays"},
            ],
            "limit": 10,
        },
        now=NOW,
    )
    by_ticker = {row["ticker"]: row for row in out["recommendations"]}
    assert set(by_ticker) == {"KXNFLGAME-25SEP28KC", "KXHIGHNY-26SEP28", "KXBTCY-26", "PLAIN-TAKE"}
    nfl = by_ticker["KXNFLGAME-25SEP28KC"]
    assert nfl["side_exec"] == "maker"
    assert nfl["fee_cents_est"] == "1"
    assert nfl["m_maker"] == "1"
    assert nfl["fee_m_source"] == "catalog"
    assert "take_profit" not in nfl
    weather = by_ticker["KXHIGHNY-26SEP28"]
    assert weather["fee_cents_est"] == "0"
    assert weather["fee_m_source"] == "inference"
    assert weather["m_taker"] == "1"
    assert weather["m_maker"] == "0"
    assert weather["wx_gap_pp"] == "12"
    assert "wx_gap_pp" not in nfl
    crypto = by_ticker["KXBTCY-26"]
    assert crypto["side_exec"] == "taker"
    assert crypto["fee_cents_est"] == "0"
    assert crypto["m_taker"] == "0"
    assert crypto["fee_m_source"] == "catalog"
    gap = Decimal(crypto["edge_net_cents"]) - Decimal(by_ticker["PLAIN-TAKE"]["edge_net_cents"])
    assert gap == Decimal("1.00")
    narrow = find_best_bets(
        {
            "beliefs": [{
                "ticker": "KXHIGHNY-26SEP28",
                "side": "yes",
                "confidence": 0.70,
                "evidence": "a three point gap still ranks",
                "wx_gap_pp": 3,
            }]
        },
        now=NOW,
    )
    assert narrow["recommendations"][0]["wx_gap_pp"] == "3"
    assert narrow["counts"].get("skipped_flb", 0) == 0


def test_belief_rejects_corr_group_none_and_a_non_numeric_weather_gap() -> None:
    with pytest.raises(RuntimeError, match="corr_group must be a snake_case risk driver"):
        parse_beliefs([{
            "ticker": "KXHIGHNY-26SEP28",
            "side": "yes",
            "confidence": 0.7,
            "corr_group": "none",
        }])
    with pytest.raises(RuntimeError, match="wx_gap_pp must be a number"):
        parse_beliefs([{
            "ticker": "KXHIGHNY-26SEP28",
            "side": "yes",
            "confidence": 0.7,
            "wx_gap_pp": "wide",
        }])
