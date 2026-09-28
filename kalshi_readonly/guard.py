"""Confirm-only stake guardrail. Safe mode defaults on, like a printer lock.

Mutating tools stay unregistered and refuse until KALSHI_SAFE_MODE is an
explicit off value. This module never sets confirm.
"""

from __future__ import annotations

import os

SAFE_MODE_ENV = "KALSHI_SAFE_MODE"
_SAFE_OFF = frozenset({"0", "false", "no", "off"})

SAFE_MODE_ERROR = (
    "refusing: KALSHI_SAFE_MODE is on. "
    "Mutating trade tools stay unregistered until KALSHI_SAFE_MODE=0"
)
CONFIRM_ERROR = "refusing: confirm must be true"


def safe_mode_enabled() -> bool:
    """True unless KALSHI_SAFE_MODE is 0, false, no, or off.

    Missing, empty, and every other value stay on so a typo cannot enable trading.
    """
    raw = os.environ.get(SAFE_MODE_ENV, "1")
    return raw.strip().lower() not in _SAFE_OFF


def require_mutation(args: dict) -> None:
    """Refuse unless safe mode is off and the caller passed boolean confirm true."""
    if safe_mode_enabled():
        raise RuntimeError(SAFE_MODE_ERROR)
    if not isinstance(args, dict) or args.get("confirm") is not True:
        raise RuntimeError(CONFIRM_ERROR)
