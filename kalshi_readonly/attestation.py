"""Washington region attestation read from a note the server already has.

Nothing here invents an expiry. The first file that exists wins:

1. ``KALSHI_WASHINGTON_ATTESTATION_PATH`` when that variable is set
2. ``$KALSHI_STATE_DIR/washington-attestation.json`` (default state dir)
3. The Finance Engineer note shipped next to this module

A missing or unreadable note is null with a reason. ``expires_in_hours`` and
``expired`` are computed from ``expires_at`` and the clock.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from kalshi_readonly.ledger import state_root

_BUNDLED = Path(__file__).resolve().parent / "washington_attestation.json"
_STATE_NAME = "washington-attestation.json"
_MISSING = "Washington region attestation note is not available"
_UNREADABLE = "Washington region attestation note could not be read"
_NO_EXPIRY = "Washington region attestation note has no expires_at"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _missing(reason: str) -> dict:
    return {"washington_attestation": None, "washington_attestation_reason": reason}


def _locate() -> tuple[Path | None, str]:
    override = (os.environ.get("KALSHI_WASHINGTON_ATTESTATION_PATH") or "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            return None, f"{_MISSING} ({path})"
        return path, ""
    state_path = state_root() / _STATE_NAME
    if state_path.is_file():
        return state_path, ""
    if _BUNDLED.is_file():
        return _BUNDLED, ""
    return None, _MISSING


def _parse_expires(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(_NO_EXPIRY)
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(_NO_EXPIRY) from None
    if parsed.tzinfo is None:
        raise ValueError(_NO_EXPIRY)
    return parsed


def _hours_until(expires: datetime, now: datetime) -> float:
    seconds = Decimal(str((expires - now).total_seconds()))
    hours = (seconds / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return float(hours)


def _read(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ValueError(_UNREADABLE) from None
    if not isinstance(payload, dict):
        raise ValueError(_UNREADABLE)
    region = payload.get("region")
    if not isinstance(region, str) or not region.strip():
        region = "Washington"
    return {"region": region.strip(), "expires": _parse_expires(payload.get("expires_at"))}


def load_washington_attestation() -> dict:
    """Attestation block for ``exchange_status``. Null when no note can be read."""
    path, reason = _locate()
    if path is None:
        return _missing(reason)
    try:
        note = _read(path)
    except ValueError as exc:
        return _missing(str(exc))
    expires = note["expires"]
    now = _now()
    expired = now >= expires
    return {
        "washington_attestation": {
            "region": note["region"],
            "status": "expired" if expired else "valid",
            "expires_at": expires.isoformat(),
            "expires_in_hours": _hours_until(expires, now),
            "expired": expired,
        },
        "washington_attestation_reason": None,
    }
