"""Sleeve caps: $2 until the book has won, $15 ceiling, 15% per market, corr_group."""

from decimal import Decimal

import pytest

from kalshi_readonly.caps import (
    assess_history,
    enforce_open,
    load_caps,
    reset_ledger,
)


@pytest.fixture(autouse=True)
def _clear_ledger():
    reset_ledger()
    yield
    reset_ledger()


def _book(**overrides):
    base = {
        "by_ticker": {},
        "by_group": {},
        "daily": Decimal("0"),
        "history": {"allows_scale": False, "closed_trades": 0, "net_realized": Decimal("0")},
    }
    base.update(overrides)
    return base


def test_ceiling_cannot_be_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KALSHI_MAX_DOLLARS_PER_TRADE", raising=False)
    assert load_caps()["ceiling"] == Decimal("15")
    assert load_caps()["default"] == Decimal("2")
    monkeypatch.setenv("KALSHI_MAX_DOLLARS_PER_TRADE", "100")
    assert load_caps()["ceiling"] == Decimal("15")
    monkeypatch.setenv("KALSHI_MAX_GROUP_FRACTION", "0.5")
    assert load_caps()["group_fraction"] == Decimal("0.30")


def test_default_blocks_a_raise_until_history_is_profitable() -> None:
    with pytest.raises(RuntimeError, match="profitable"):
        enforce_open(notional=Decimal("2.01"), price=Decimal("0.40"), ticker="KX", book=_book())
    enforce_open(notional=Decimal("2"), price=Decimal("0.40"), ticker="KX", book=_book())
    won = _book(history={"allows_scale": True, "closed_trades": 3, "net_realized": Decimal("1")})
    with pytest.raises(RuntimeError, match=r"hard max \$15"):
        enforce_open(notional=Decimal("16"), price=Decimal("0.80"), ticker="KX", book=won)
    with pytest.raises(RuntimeError, match="fixed_2"):
        enforce_open(notional=Decimal("6"), price=Decimal("0.15"), ticker="KX", book=won)
    enforce_open(notional=Decimal("6"), price=Decimal("0.60"), ticker="KX", book=won)


def test_market_fraction_and_corr_group() -> None:
    crowded = _book(by_ticker={"KX": Decimal("10.65")})
    with pytest.raises(RuntimeError, match="15%"):
        enforce_open(notional=Decimal("1"), price=Decimal("0.40"), ticker="KX", book=crowded)
    grouped = _book(by_group={"us_election_2026": Decimal("21.30")})
    with pytest.raises(RuntimeError, match="corr_group"):
        enforce_open(
            notional=Decimal("2"),
            price=Decimal("0.40"),
            ticker="NEW",
            book=grouped,
            corr_group="us_election_2026",
        )


def test_history_needs_three_closes_and_a_positive_net() -> None:
    positions = {"market_positions": [{"ticker": "OLD", "realized_pnl_dollars": "2.50", "fees_paid_dollars": "0.10"}]}
    fills = {"fills": [{"action": "sell"}, {"action": "sell"}, {"action": "sell"}]}
    won = assess_history(positions, fills)
    assert won["allows_scale"] is True
    assert won["net_realized"] == Decimal("2.40")
    short = assess_history(positions, {"fills": [{"action": "sell"}]})
    assert short["closed_trades"] == 1
    assert short["allows_scale"] is False
