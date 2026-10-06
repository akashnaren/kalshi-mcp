"""Ticker lookup, decision enums, and Washington attestation on the read tools."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from kalshi_readonly.attestation import load_washington_attestation
from kalshi_readonly.ledger import APPEND_DECISION_TOOL, FLB_BANDS, RANK_SOURCES, append_decision
from kalshi_readonly.tools import exchange_status, list_markets

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
    "flb_band": "25–75¢",
    "stake_dollars": 2,
    "count": 4,
    "price": 0.5,
    "gates_passed": ["beliefs_present"],
    "gates_failed": [],
    "failed_gate": None,
    "logic_path": ["beliefs_present"],
    "rank_source": "find_best_bets",
    "confirm": True,
}


def _state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("KALSHI_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("KALSHI_LEDGER_PATH", raising=False)
    monkeypatch.delenv("KALSHI_WASHINGTON_ATTESTATION_PATH", raising=False)


def test_list_markets_ticker_uses_single_market_get_and_keeps_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, dict | None]] = []

    def fake_public_get(path: str, query=None):
        seen.append((path, query))
        return {
            "market": {
                "ticker": "KXHIGHCHI-26SEP30",
                "title": "Chicago high",
                "status": "settled",
                "result": "yes",
                "yes_bid": None,
                "yes_ask": None,
                "volume": 10,
                "settlement_value_dollars": "1.0000",
                "settlement_ts": "2026-10-01T19:00:00Z",
                "expiration_value": "78",
                "close_time": "2026-10-01T12:00:00Z",
            }
        }

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    out = list_markets({"ticker": "KXHIGHCHI-26SEP30"})
    assert seen == [("/markets/KXHIGHCHI-26SEP30", None)]
    assert out["count"] == 1
    assert out["filters"]["ticker"] == "KXHIGHCHI-26SEP30"
    row = out["markets"][0]
    assert row["ticker"] == "KXHIGHCHI-26SEP30"
    assert row["status"] == "settled"
    assert row["result"] == "yes"
    assert row["settlement_value_dollars"] == "1.0000"
    assert row["settlement_ts"] == "2026-10-01T19:00:00Z"
    assert row["expiration_value"] == "78"
    assert "settlement_value" not in row


def test_list_markets_quotes_ticker_and_list_rows_include_result(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[tuple[str, dict | None]] = []

    def fake_public_get(path: str, query=None):
        seen.append((path, dict(query) if query else None))
        if path.startswith("/markets/"):
            return {"market": {"ticker": "KX A/B", "status": "open", "result": ""}}
        return {
            "markets": [
                {"ticker": "OPEN-1", "status": "open", "result": "", "yes_ask": 40, "yes_bid": 38},
                {
                    "ticker": "SETTLED-1",
                    "status": "settled",
                    "result": "no",
                    "yes_ask": 1,
                    "yes_bid": 1,
                    "settlement_value": 0,
                },
            ],
            "cursor": "",
        }

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    quoted = list_markets({"ticker": "KX A/B", "status": "open"})
    assert seen[0][0] == "/markets/KX%20A%2FB"
    assert quoted["markets"][0]["result"] == ""
    assert quoted["markets"][0]["status"] == "open"

    listed = list_markets({"status": "settled", "liquid": False, "limit": 10})
    assert seen[1][0] == "/markets"
    assert "ticker" not in seen[1][1]
    assert "tickers" not in seen[1][1]
    assert seen[1][1]["status"] == "settled"
    by_ticker = {row["ticker"]: row for row in listed["markets"]}
    assert by_ticker["OPEN-1"]["result"] == ""
    assert "settlement_value" not in by_ticker["OPEN-1"]
    assert by_ticker["SETTLED-1"]["result"] == "no"
    assert by_ticker["SETTLED-1"]["status"] == "settled"
    assert by_ticker["SETTLED-1"]["settlement_value"] == 0


def test_append_decision_names_price_band_and_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _state(monkeypatch, tmp_path)
    record = APPEND_DECISION_TOOL["inputSchema"]["properties"]["record"]
    assert record["properties"]["flb_band"]["enum"] == list(FLB_BANDS)
    assert record["properties"]["rank_source"]["enum"] == list(RANK_SOURCES)
    description = APPEND_DECISION_TOOL["description"]
    for value in (*FLB_BANDS, *RANK_SOURCES):
        assert value in description

    bad_band = dict(_PLACE)
    bad_band["flb_band"] = "middle"
    with pytest.raises(RuntimeError, match="flb_band must be one of the allowed values") as band_error:
        append_decision({"record": bad_band})
    for band in FLB_BANDS:
        assert band in str(band_error.value)

    bad_source = dict(_PLACE)
    bad_source["rank_source"] = "gut"
    with pytest.raises(RuntimeError, match="rank_source must be one of the allowed values") as source_error:
        append_decision({"record": bad_source})
    assert "find_best_bets" in str(source_error.value)
    assert "fe_manual" in str(source_error.value)
    assert not (tmp_path / "ledger" / "decisions.jsonl").exists()

    accepted = dict(_PLACE)
    accepted["flb_band"] = ">=90¢"
    written = append_decision({"record": accepted})
    assert written["appended"] is True
    assert written["action"] == "place"


def test_exchange_status_reports_washington_attestation(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _state(monkeypatch, tmp_path)
    calls: list[str] = []

    def fake_public_get(path: str, query=None):
        calls.append(path)
        return {"exchange_active": True, "trading_active": True}

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    expires = datetime(2026, 10, 7, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "kalshi_readonly.attestation._now",
        lambda: expires - timedelta(hours=2),
    )
    out = exchange_status()
    assert calls == ["/exchange/status"]
    assert out["exchange_active"] is True
    assert out["trading_active"] is True
    note = out["washington_attestation"]
    assert note["region"] == "Washington"
    assert note["status"] == "valid"
    assert note["expires_at"] == "2026-10-06T18:00:00-07:00"
    assert note["expires_in_hours"] == 2.0
    assert note["expired"] is False
    assert out["washington_attestation_reason"] is None

    monkeypatch.setattr(
        "kalshi_readonly.attestation._now",
        lambda: expires + timedelta(minutes=30),
    )
    later = exchange_status()
    assert later["washington_attestation"]["expired"] is True
    assert later["washington_attestation"]["status"] == "expired"
    assert later["washington_attestation"]["expires_in_hours"] == -0.5


def test_exchange_status_null_when_attestation_note_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _state(monkeypatch, tmp_path)
    missing = tmp_path / "no-such-note.json"
    monkeypatch.setenv("KALSHI_WASHINGTON_ATTESTATION_PATH", str(missing))

    def fake_public_get(path: str, query=None):
        return {"exchange_active": True, "trading_active": False}

    monkeypatch.setattr("kalshi_readonly.tools.public_get", fake_public_get)
    out = exchange_status()
    assert out["washington_attestation"] is None
    assert "not available" in out["washington_attestation_reason"]
    assert str(missing) in out["washington_attestation_reason"]
    assert out["exchange_active"] is True

    blank = tmp_path / "blank.json"
    blank.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("KALSHI_WASHINGTON_ATTESTATION_PATH", str(blank))
    empty = load_washington_attestation()
    assert empty["washington_attestation"] is None
    assert empty["washington_attestation_reason"] == "Washington region attestation note has no expires_at"


def test_state_attestation_note_overrides_the_packaged_expiry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _state(monkeypatch, tmp_path)
    note = tmp_path / "washington-attestation.json"
    note.write_text(
        '{"region":"Washington","expires_at":"2026-11-01T18:00:00-07:00"}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "kalshi_readonly.attestation._now",
        lambda: datetime(2026, 11, 2, 0, 0, tzinfo=timezone.utc),
    )
    loaded = load_washington_attestation()
    assert loaded["washington_attestation"]["expires_at"] == "2026-11-01T18:00:00-07:00"
    assert loaded["washington_attestation"]["expired"] is False
    assert loaded["washington_attestation"]["expires_in_hours"] == 1.0
