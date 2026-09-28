"""Safe mode and confirm gates for trade writes.

Same shape as bambu-mcp: safe mode defaults on, confirm does not unlock it,
and the server never sets confirm.
"""

from __future__ import annotations

import os

TRADE_WRITES = frozenset({"place_order", "cancel_order", "amend_order", "decrease_order"})

_OFF = frozenset({"0", "false", "off", "no"})


def parse_safe_mode(raw: str | None) -> bool:
    """On unless the operator opts out. Only 0, false, off, and no unlock writes."""
    if raw is None or raw.strip() == "":
        return True
    return raw.strip().lower() not in _OFF


def safe_mode() -> bool:
    """True unless the operator set KALSHI_SAFE_MODE=0."""
    return parse_safe_mode(os.environ.get("KALSHI_SAFE_MODE"))


def assert_safe_mode(tool: str) -> None:
    if not safe_mode() or tool not in TRADE_WRITES:
        return
    raise RuntimeError(
        f"{tool} is blocked by safe mode. KALSHI_SAFE_MODE defaults to 1. "
        "Set KALSHI_SAFE_MODE=0 to unlock trade tools. "
        "Trade tools still require confirm: true plus an explicit human ask. Never auto-confirm."
    )


def assert_confirmed(tool: str, confirm: object) -> None:
    if tool not in TRADE_WRITES or confirm is True:
        return
    raise RuntimeError(
        f"{tool} is confirm-gated. Ask the operator, then retry with confirm: true. Never set confirm yourself."
    )


def guard_trade(tool: str, confirm: object) -> None:
    """Safe mode first, then confirm. Confirm does not unlock safe mode."""
    assert_safe_mode(tool)
    assert_confirmed(tool, confirm)
