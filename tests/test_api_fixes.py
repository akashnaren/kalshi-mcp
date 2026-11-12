"""Scan, expiry, fee schedule, and list_markets fixes. HTTP is mocked."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from kalshi_readonly.fees import clear_series_fee_cache
from kalshi_readonly.markets import clear_market_cache
from kalshi_readonly.recommend import find_best_bets
from kalshi_readonly.score import parse_beliefs, parse_options, rank_markets, resolved_fees
from kalshi_readonly.tools import list_markets, registered_tools

NOW = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
TICKER = "KXHIGHNY-26OCT08-T70"


@pytest.fixture(autouse=True)
def _clear_markets():
    clear_market_cache()
    yield
    clear_market_cache()


def _market(**overrides) -> dict:
    row = {
        "ticker": TICKER,
        "event_ticker": "KXHIGHNY-26OCT08",
        "series_ticker": "KXHIGHNY",
        "title": "NYC high",
        "status": "active",
        "market_type": "binary",
        "yes_ask_dollars": "0.4000",
        "yes_bid_dollars": "0.3800",
        "no_ask_dollars": "0.6200",
        "yes_ask_size_fp": "20.00",
        "no_ask_size_fp": "20.00",
        "volume_fp": "5000.00",
        "volume_24h_fp": "100.00",
        "notional_value_dollars": "1.0000",
        "close_time": "2026-09-28T16:00:00Z",
        "expected_expiration_time": "2026-10-08T15:00:00Z",
        "can_close_early": True,
    }
    row.update(overrides)
    return row


def _beliefs() -> list[dict]:
    return parse_beliefs([{
        "ticker": TICKER,
        "side": "yes",
        "confidence": 0.70,
        "evidence": "city forecast",
    }])


def test_expiry_uses_expected_expiration_when_close_can_move_early() -> None:
    options = parse_options({})
    ranked, counts, _missing, _skipped = rank_markets([_market()], _beliefs(), options, NOW)
    assert counts["skipped_expiry"] == 0
    assert ranked[0]["hours_to_expiry"] == "240.00"
    assert ranked[0]["days_to_res"] == "10.00"
    early = _market()
    early.pop("expected_expiration_time")
    ranked_early, counts_early, _missing, _skipped = rank_markets([early], _beliefs(), options, NOW)
    assert ranked_early == []
    assert counts_early["skipped_expiry"] == 1


def test_close_time_is_the_fallback_when_expected_expiration_is_missing() -> None:
    options = parse_options({})
    market = _market(close_time="2026-10-08T15:00:00Z")
    market.pop("expected_expiration_time")
    ranked, counts, _missing, _skipped = rank_markets([market], _beliefs(), options, NOW)
    assert counts["skipped_expiry"] == 0
    assert ranked[0]["days_to_res"] == "10.00"


def _install_series(monkeypatch: pytest.MonkeyPatch, series: dict) -> list[str]:
    calls: list[str] = []

    def fetch(ticker: str) -> dict:
        calls.append(ticker)
        return {"series": {"ticker": ticker, **series}}

    monkeypatch.setattr("kalshi_readonly.fees.fetch_series", fetch)
    return calls


def test_fetch_series_requests_the_series_document(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def public_get(path: str, query=None):
        seen.append(path)
        assert query is None
        return {"series": {"ticker": "KXHIGHNY", "fee_type": "quadratic", "fee_multiplier": 1}}

    monkeypatch.setattr("kalshi_readonly.fees.public_get", public_get)
    assert resolved_fees({"ticker": "KXHIGHNY-26OCT08-T70", "series_ticker": "KXHIGHNY"}, NOW) == (
        Decimal(1),
        Decimal(0),
        "series",
    )
    assert seen == ["/series/KXHIGHNY"]


def test_quadratic_series_has_no_maker_fee_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_series(monkeypatch, {"fee_type": "quadratic", "fee_multiplier": 1})
    market = _market()
    assert resolved_fees(market, NOW) == (Decimal(1), Decimal(0), "series")
    assert resolved_fees(market, NOW) == (Decimal(1), Decimal(0), "series")
    assert calls == ["KXHIGHNY"]


def test_live_series_beats_the_payload_multiplier_and_the_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_series(monkeypatch, {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1})
    market = _market(fee_multiplier="9")
    assert resolved_fees(market, NOW) == (Decimal(1), Decimal(1), "series")
    _install_series(monkeypatch, {"fee_type": "quadratic_with_combo_maker_fees", "fee_multiplier": 1})
    clear_series_fee_cache()
    assert resolved_fees(market, NOW) == (Decimal(1), Decimal(2), "series")


def test_fee_waiver_zeroes_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_series(
        monkeypatch,
        {
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "fee_waiver_expiration_time": "2026-12-01T00:00:00Z",
        },
    )
    assert resolved_fees(_market(), NOW) == (Decimal(0), Decimal(0), "waiver")
    assert calls == ["KXHIGHNY"]
    clear_calls = _install_series(monkeypatch, {"fee_type": "quadratic", "fee_multiplier": 1})
    clear_series_fee_cache()
    waived = _market(fee_waiver_expiration_time="2026-12-01T00:00:00Z")
    assert resolved_fees(waived, NOW) == (Decimal(0), Decimal(0), "waiver")
    assert clear_calls == []


def test_expired_waiver_uses_the_series_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_series(
        monkeypatch,
        {
            "fee_type": "quadratic",
            "fee_multiplier": 1,
            "fee_waiver_expiration_time": "2026-01-01T00:00:00Z",
        },
    )
    assert resolved_fees(_market(), NOW) == (Decimal(1), Decimal(0), "series")


def test_series_failure_falls_back_to_the_prefix_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_ticker: str) -> dict:
        raise RuntimeError("series down")

    monkeypatch.setattr("kalshi_readonly.fees.fetch_series", boom)
    assert resolved_fees(_market(), NOW) == (Decimal(1), Decimal(0), "inference")
    assert resolved_fees(_market(fee_multiplier="2"), NOW) == (Decimal(2), Decimal(2), "payload")


def test_flat_series_does_not_invent_a_quadratic_fee(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_series(monkeypatch, {"fee_type": "flat", "fee_multiplier": 0})
    assert resolved_fees(_market(), NOW) == (Decimal(1), Decimal(0), "inference")


def test_ranked_row_uses_the_live_schedule(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_series(monkeypatch, {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 2})
    ranked, _counts, _missing, _skipped = rank_markets([_market()], _beliefs(), parse_options({}), NOW)
    assert ranked[0]["fee_m_source"] == "series"
    assert ranked[0]["m_taker"] == "2"
    assert ranked[0]["m_maker"] == "2"


def _signed(monkeypatch: pytest.MonkeyPatch, pages: list[dict]):
    calls: list[tuple[str, dict]] = []

    def auth_get(path: str, query: dict | None = None):
        calls.append((path, dict(query or {})))
        return pages[len(calls) - 1]

    def public_get(*_args, **_kwargs):
        raise AssertionError("scan reads must be signed")

    monkeypatch.setattr("kalshi_readonly.markets.auth_get", auth_get)
    monkeypatch.setattr("kalshi_readonly.markets.public_get", public_get)
    return calls


def test_series_scan_paginates_events_without_close_timestamps(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _signed(
        monkeypatch,
        [
            {
                "events": [{
                    "series_ticker": "KXHIGHNY",
                    "markets": [_market(ticker="KXHIGHNY-26OCT08-T70")],
                }],
                "cursor": "page-2",
            },
            {
                "events": [{
                    "series_ticker": "KXHIGHNY",
                    "markets": [_market(ticker="KXHIGHNY-26OCT08-T71", close_time="2026-10-08T15:00:00Z")],
                }],
                "cursor": "",
            },
        ],
    )
    out = find_best_bets(
        {"series_tickers": ["kxhighny", "kxhighny"], "max_pages": 2, "page_size": 50},
        now=NOW,
    )
    assert [path for path, _query in calls] == ["/events", "/events"]
    assert calls[0][1]["series_ticker"] == "KXHIGHNY"
    assert calls[0][1]["with_nested_markets"] == "true"
    assert calls[0][1]["status"] == "open"
    assert calls[0][1]["limit"] == 50
    assert "min_close_ts" not in calls[0][1]
    assert "max_close_ts" not in calls[0][1]
    assert "cursor" not in calls[0][1]
    assert calls[1][1]["cursor"] == "page-2"
    assert [row["ticker"] for row in out["research_queue"]] == [
        "KXHIGHNY-26OCT08-T70",
        "KXHIGHNY-26OCT08-T71",
    ]


def test_two_series_each_get_a_page(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _signed(
        monkeypatch,
        [
            {"events": [{"series_ticker": "KXHIGHNY", "markets": [_market()]}], "cursor": "ignored"},
            {
                "events": [{
                    "series_ticker": "KXHIGHCHI",
                    "markets": [_market(ticker="KXHIGHCHI-26OCT08-T70", series_ticker="KXHIGHCHI")],
                }],
                "cursor": "",
            },
        ],
    )
    find_best_bets(
        {"series_tickers": "KXHIGHNY,KXHIGHCHI", "max_pages": 1, "page_size": 25},
        now=NOW,
    )
    assert [query["series_ticker"] for _path, query in calls] == ["KXHIGHNY", "KXHIGHCHI"]
    assert all(query["limit"] == 25 for _path, query in calls)


def test_series_page_size_1000_uses_markets_and_unscoped_stays_at_200(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _signed(
        monkeypatch,
        [{"markets": [_market(close_time="2026-10-08T15:00:00Z")], "cursor": ""}],
    )
    find_best_bets({"series_tickers": ["KXHIGHNY"], "page_size": 1000, "max_pages": 1}, now=NOW)
    assert calls[0][0] == "/markets"
    assert calls[0][1]["limit"] == 1000
    assert calls[0][1]["series_ticker"] == "KXHIGHNY"
    assert calls[0][1]["status"] == "open"
    assert "min_close_ts" not in calls[0][1]
    assert "max_close_ts" not in calls[0][1]
    with pytest.raises(RuntimeError, match="page_size must be an integer from 1 to 200"):
        find_best_bets({"page_size": 1000}, now=NOW)
    with pytest.raises(RuntimeError, match="series_tickers must be series tickers"):
        find_best_bets({"series_tickers": ["KXHIGH*"]}, now=NOW)


def test_unscoped_scan_is_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _signed(monkeypatch, [{"markets": [], "cursor": ""}])
    find_best_bets({"max_pages": 1, "page_size": 10}, now=NOW)
    assert calls == [("/markets", {"status": "open", "limit": 10, "mve_filter": "exclude"})]


def test_list_markets_reads_fp_fields_and_new_categories(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_public_get(path: str, query=None):
        assert path == "/markets"
        return {
            "markets": [
                {
                    "ticker": "KXELECTWIN-28",
                    "series_ticker": "KXELECT",
                    "title": "election",
                    "status": "open",
                    "yes_bid_dollars": "0.3800",
                    "yes_ask_dollars": "0.4000",
                    "last_price_dollars": "0.3900",
                    "volume": None,
                    "volume_24h": None,
                    "volume_fp": "12.00",
                    "volume_24h_fp": "3.00",
                    "open_interest_fp": "4.00",
                    "yes_ask_size_fp": "8.00",
                },
                {
                    "ticker": "KXOSCARBEST-26",
                    "series_ticker": "KXOSCAR",
                    "yes_bid_dollars": "0.1800",
                    "yes_ask_dollars": "0.2000",
                    "volume_fp": "9.00",
                    "volume_24h_fp": "1.00",
                },
                {
                    "ticker": "KXHIGHNY-26",
                    "series_ticker": "KXHIGH",
                    "yes_bid_dollars": "0.2800",
                    "yes_ask_dollars": "0.3000",
                    "volume_fp": "8.00",
                },
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    elections = list_markets({"category": "elections"})
    assert [row["ticker"] for row in elections["markets"]] == ["KXELECTWIN-28"]
    row = elections["markets"][0]
    assert row["volume_fp"] == "12.00"
    assert row["volume"] == "12.00"
    assert row["volume_24h_fp"] == "3.00"
    assert row["volume_24h"] == "3.00"
    assert row["open_interest_fp"] == "4.00"
    assert row["last_price_dollars"] == "0.3900"
    assert row["yes_ask_dollars"] == "0.4000"
    entertainment = list_markets({"category": "entertainment"})
    assert [row["ticker"] for row in entertainment["markets"]] == ["KXOSCARBEST-26"]
    with pytest.raises(RuntimeError, match="elections"):
        list_markets({"category": "nope"})
    names = [tool["name"] for tool in registered_tools()]
    assert "shard_balances" in names
    assert "transfer_between_shards" not in names
