"""Per-shard balances and same-account shard transfers.

GET /portfolio/balance returns balance_breakdown, one row per exchange shard.
https://docs.kalshi.com/api-reference/portfolio/get-balance
https://docs.kalshi.com/getting_started/exchange_sharding

When that breakdown is omitted (a subaccount-restricted key), shard rows come
from GET /portfolio/subaccounts/balances, summed by exchange_index.
https://docs.kalshi.com/api-reference/portfolio/get-all-subaccount-balances

Moving funds uses POST /portfolio/intra_exchange_instance_transfer.
The amount is centicents: 1 dollar = 10_000 centicents.
https://docs.kalshi.com/api-reference/portfolio/intra-account-transfer

This module never calls a deposit, withdrawal, or subaccount-transfer route.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation

from kalshi_readonly.guard import require_mutation
from kalshi_readonly.http import auth_call, auth_get

MAX_SHARD_TRANSFER_DOLLARS = Decimal("15")
_CENTICENTS_PER_DOLLAR = Decimal(10000)
_BALANCE_PATH = "/portfolio/balance"
_SUBACCOUNT_BALANCES_PATH = "/portfolio/subaccounts/balances"
_TRANSFER_PATH = "/portfolio/intra_exchange_instance_transfer"
_SHARD_MAX = 100

_CONFIRM = {
    "type": "boolean",
    "description": "Required boolean true from the caller. The server never sets this.",
}


def _dollar(amount: Decimal) -> str:
    return format(amount, "f")


def _shard_rows(items: object, *, balance_key: str = "balance") -> list[dict]:
    if not isinstance(items, list):
        return []
    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("exchange_index")
        balance = item.get(balance_key)
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not isinstance(balance, str) or not balance.strip():
            continue
        rows.append({"shard_id": index, "balance": balance.strip()})
    rows.sort(key=lambda row: row["shard_id"])
    return rows


def _sum_subaccounts(payload: dict) -> list[dict]:
    items = payload.get("subaccount_balances")
    if not isinstance(items, list):
        return []
    totals: dict[int, Decimal] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("exchange_index")
        raw = item.get("balance")
        if isinstance(index, bool) or not isinstance(index, int):
            continue
        if not isinstance(raw, str):
            continue
        try:
            amount = Decimal(raw.strip())
        except InvalidOperation:
            continue
        totals[index] = totals.get(index, Decimal(0)) + amount
    return [{"shard_id": index, "balance": _dollar(totals[index])} for index in sorted(totals)]


def shard_balances(args: dict | None = None) -> dict:
    """Each exchange shard id and its dollar balance for this account.

    Auth required. Does not transfer, deposit, or withdraw.
    """
    del args
    balance = auth_get(_BALANCE_PATH)
    if not isinstance(balance, dict):
        raise RuntimeError("unexpected balance payload")
    shards = _shard_rows(balance.get("balance_breakdown"))
    source = "balance_breakdown"
    if not shards:
        subaccounts = auth_get(_SUBACCOUNT_BALANCES_PATH)
        if not isinstance(subaccounts, dict):
            raise RuntimeError("unexpected subaccount balance payload")
        shards = _sum_subaccounts(subaccounts)
        source = "subaccount_balances"
    out: dict = {"shards": shards, "source": source}
    dollars = balance.get("balance_dollars")
    if isinstance(dollars, str) and dollars.strip():
        out["balance_dollars"] = dollars.strip()
    return out


def _shard_id(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(f"{field} must be an exchange shard id")
    if value < 0 or value > _SHARD_MAX:
        raise RuntimeError(f"{field} must be an exchange shard id")
    return value


def _amount(value: object) -> tuple[int, Decimal]:
    if isinstance(value, bool) or value is None:
        raise RuntimeError("amount must be a positive dollar amount up to 15")
    try:
        if isinstance(value, str):
            amount = Decimal(value.strip())
        elif isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            amount = Decimal(str(value))
        else:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        raise RuntimeError("amount must be a positive dollar amount up to 15") from None
    if amount <= 0 or amount > MAX_SHARD_TRANSFER_DOLLARS:
        raise RuntimeError("amount must be a positive dollar amount up to 15")
    scaled = amount * _CENTICENTS_PER_DOLLAR
    if scaled != scaled.to_integral_value():
        raise RuntimeError("amount must be a multiple of 0.0001 dollars")
    return int(scaled), amount


def _parse_dollars(text: str) -> Decimal | None:
    try:
        return Decimal(text.strip())
    except (InvalidOperation, ValueError):
        return None


def transfer_between_shards(args: dict | None = None) -> dict:
    """Move dollars between two shards of this Kalshi account.

    confirm must be boolean true, and safe mode must be off. Source and
    destination must both appear on the account's shard list. The amount is
    positive and at most $15, the same hard ceiling as one trade.
    """
    args = args or {}
    require_mutation(args)
    source = _shard_id(args.get("source_shard"), "source_shard")
    destination = _shard_id(args.get("destination_shard"), "destination_shard")
    if source == destination:
        raise RuntimeError("source_shard and destination_shard must be different shards")
    centicents, dollars = _amount(args.get("amount"))
    before = shard_balances({})
    known = {row["shard_id"]: row["balance"] for row in before["shards"]}
    if source not in known or destination not in known:
        raise RuntimeError("source_shard and destination_shard must both be shards of this account")
    available = _parse_dollars(known[source])
    if available is not None and available < dollars:
        raise RuntimeError("source shard balance is below the transfer amount")
    body = {
        "source": "event_contract",
        "destination": "event_contract",
        "amount": centicents,
        "source_exchange_shard": source,
        "destination_exchange_shard": destination,
        "client_transfer_id": str(uuid.uuid4()),
    }
    result = auth_call("POST", _TRANSFER_PATH, body=body)
    after = shard_balances({})
    transfer_id = result.get("transfer_id") if isinstance(result, dict) else None
    return {
        "transfer_id": transfer_id if isinstance(transfer_id, str) else None,
        "source_shard": source,
        "destination_shard": destination,
        "amount_dollars": _dollar(dollars),
        "amount_centicents": centicents,
        "before": before["shards"],
        "after": after["shards"],
        "note": (
            "Accepted by POST /portfolio/intra_exchange_instance_transfer. "
            "Kalshi processes the transfer asynchronously, so after is the next balance read."
        ),
    }


SHARD_BALANCE_TOOL = {
    "name": "shard_balances",
    "description": (
        "Read this Kalshi account's balance on each exchange shard. "
        "Uses GET /portfolio/balance balance_breakdown (exchange_index is the shard id). "
        "If that breakdown is omitted, sums GET /portfolio/subaccounts/balances by exchange_index. "
        "Auth required. Does not transfer, deposit, or withdraw."
    ),
    "inputSchema": {"type": "object", "properties": {}},
}

SHARD_TRANSFER_TOOL = {
    "name": "transfer_between_shards",
    "description": (
        "Move funds between two shards of the same Kalshi account "
        "(POST /portfolio/intra_exchange_instance_transfer). "
        "source and destination are event_contract shards. "
        "source_shard and destination_shard must both be shards on this account and must differ. "
        "amount is dollars, positive, at most 15, converted to centicents (1 dollar = 10000). "
        "Returns the shard balances from before the POST and from the next balance read. "
        "Kalshi applies the transfer asynchronously. "
        "Does not call a deposit, withdrawal, or subaccount transfer endpoint. "
        "Requires KALSHI_SAFE_MODE=0 and confirm=true (boolean). The server never sets confirm."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "source_shard": {"type": "integer", "description": "Exchange shard id to debit. 0 to 100."},
            "destination_shard": {"type": "integer", "description": "Exchange shard id to credit. 0 to 100."},
            "amount": {"type": "string", "description": "Dollars to move. Positive, at most 15."},
            "confirm": _CONFIRM,
        },
        "required": ["source_shard", "destination_shard", "amount", "confirm"],
    },
}
