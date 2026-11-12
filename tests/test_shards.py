"""Same-account shard reads and transfers. No live orders or deposits."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from kalshi_readonly.caps import HARD_TRADE
from kalshi_readonly.guard import CONFIRM_ERROR, SAFE_MODE_ERROR
from kalshi_readonly.shards import MAX_SHARD_TRANSFER_DOLLARS, shard_balances, transfer_between_shards
from kalshi_readonly.tools import registered_tools

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _balance(*rows: tuple[int, str], dollars: str = "10.00") -> dict:
    return {
        "balance": 1000,
        "balance_dollars": dollars,
        "portfolio_value": 0,
        "updated_ts": 1,
        "balance_breakdown": [{"exchange_index": index, "balance": amount} for index, amount in rows],
    }


def test_max_transfer_matches_the_hard_trade_cap() -> None:
    assert MAX_SHARD_TRANSFER_DOLLARS == HARD_TRADE == Decimal("15")


def test_shard_balances_reads_the_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def auth_get(path: str, query=None):
        seen.append(path)
        assert query is None
        return _balance((0, "8.00"), (2, "2.00"))

    monkeypatch.setattr("kalshi_readonly.shards.auth_get", auth_get)
    out = shard_balances({})
    assert seen == ["/portfolio/balance"]
    assert out["source"] == "balance_breakdown"
    assert out["shards"] == [
        {"shard_id": 0, "balance": "8.00"},
        {"shard_id": 2, "balance": "2.00"},
    ]
    assert out["balance_dollars"] == "10.00"


def test_shard_balances_sum_subaccounts_when_breakdown_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    def auth_get(path: str, query=None):
        if path == "/portfolio/balance":
            return {"balance": 650, "balance_dollars": "6.50", "portfolio_value": 0, "updated_ts": 1}
        assert path == "/portfolio/subaccounts/balances"
        return {
            "subaccount_balances": [
                {"subaccount_number": 0, "exchange_index": 0, "balance": "4.00", "updated_ts": 1},
                {"subaccount_number": 1, "exchange_index": 0, "balance": "1.00", "updated_ts": 1},
                {"subaccount_number": 0, "exchange_index": 3, "balance": "2.50", "updated_ts": 1},
            ]
        }

    monkeypatch.setattr("kalshi_readonly.shards.auth_get", auth_get)
    out = shard_balances({})
    assert out["source"] == "subaccount_balances"
    assert out["shards"] == [
        {"shard_id": 0, "balance": "5.00"},
        {"shard_id": 3, "balance": "2.50"},
    ]


def test_transfer_refuses_without_confirm_and_does_not_call_kalshi(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args, **_kwargs):
        raise AssertionError("refused transfer must not call Kalshi")

    monkeypatch.setattr("kalshi_readonly.shards.auth_get", boom)
    monkeypatch.setattr("kalshi_readonly.shards.auth_call", boom)
    monkeypatch.delenv("KALSHI_SAFE_MODE", raising=False)
    with pytest.raises(RuntimeError, match=SAFE_MODE_ERROR):
        transfer_between_shards({
            "source_shard": 0,
            "destination_shard": 2,
            "amount": "1.00",
            "confirm": True,
        })
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")
    with pytest.raises(RuntimeError, match=CONFIRM_ERROR):
        transfer_between_shards({
            "source_shard": 0,
            "destination_shard": 2,
            "amount": "1.00",
            "confirm": False,
        })


def test_transfer_moves_between_account_shards_and_returns_balances(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")
    reads = [
        _balance((0, "8.00"), (2, "2.00")),
        _balance((0, "6.50"), (2, "3.50")),
    ]
    posts: list[tuple[str, dict]] = []

    def auth_get(path: str, query=None):
        assert path == "/portfolio/balance"
        assert "deposit" not in path and "withdraw" not in path
        return reads.pop(0)

    def auth_call(method: str, path: str, query=None, body=None):
        assert method == "POST"
        assert path == "/portfolio/intra_exchange_instance_transfer"
        assert "deposit" not in path
        assert "withdraw" not in path
        assert "subaccounts/transfer" not in path
        posts.append((path, body))
        return {"transfer_id": "tr-1"}

    monkeypatch.setattr("kalshi_readonly.shards.auth_get", auth_get)
    monkeypatch.setattr("kalshi_readonly.shards.auth_call", auth_call)
    out = transfer_between_shards({
        "source_shard": 0,
        "destination_shard": 2,
        "amount": "1.50",
        "confirm": True,
    })
    assert len(posts) == 1
    body = posts[0][1]
    assert body["source"] == "event_contract"
    assert body["destination"] == "event_contract"
    assert body["amount"] == 15000
    assert body["source_exchange_shard"] == 0
    assert body["destination_exchange_shard"] == 2
    assert _UUID.fullmatch(body["client_transfer_id"])
    assert "confirm" not in body
    assert out["transfer_id"] == "tr-1"
    assert out["amount_dollars"] == "1.50"
    assert out["amount_centicents"] == 15000
    assert out["before"] == [
        {"shard_id": 0, "balance": "8.00"},
        {"shard_id": 2, "balance": "2.00"},
    ]
    assert out["after"] == [
        {"shard_id": 0, "balance": "6.50"},
        {"shard_id": 2, "balance": "3.50"},
    ]
    assert reads == []


def test_transfer_cap_is_fifteen_dollars_and_shards_must_belong_to_the_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KALSHI_SAFE_MODE", "0")
    calls = {"post": 0, "get": 0}

    def auth_get(path: str, query=None):
        calls["get"] += 1
        return _balance((0, "20.00"), (2, "1.00"))

    def auth_call(*_args, **_kwargs):
        calls["post"] += 1
        return {"transfer_id": "tr-15"}

    monkeypatch.setattr("kalshi_readonly.shards.auth_get", auth_get)
    monkeypatch.setattr("kalshi_readonly.shards.auth_call", auth_call)
    base = {"source_shard": 0, "destination_shard": 2, "confirm": True}
    with pytest.raises(RuntimeError, match="up to 15"):
        transfer_between_shards({**base, "amount": "15.01"})
    assert calls == {"post": 0, "get": 0}
    with pytest.raises(RuntimeError, match="positive"):
        transfer_between_shards({**base, "amount": "0"})
    with pytest.raises(RuntimeError, match="different shards"):
        transfer_between_shards({**base, "destination_shard": 0, "amount": "1"})
    with pytest.raises(RuntimeError, match="shards of this account"):
        transfer_between_shards({**base, "destination_shard": 9, "amount": "1"})
    assert calls["post"] == 0
    assert calls["get"] == 1
    with pytest.raises(RuntimeError, match="below the transfer amount"):
        transfer_between_shards({**base, "source_shard": 2, "destination_shard": 0, "amount": "2"})
    assert calls["post"] == 0
    out = transfer_between_shards({**base, "amount": "15"})
    assert out["amount_centicents"] == 150000
    assert calls["post"] == 1
    names = [tool["name"] for tool in registered_tools()]
    assert "transfer_between_shards" in names
