"""Append-only decision ledger and the week / life-to-date stats on it.

The file lives under plugin-writable state (`KALSHI_STATE_DIR`, default
`~/.local/state/kalshi`). Finance Engineer mirrors that file to
`/workspace/state/kalshi/ledger/decisions.jsonl`. History is never rewritten.
A later outcome is a new line with `parent_id`.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

_PT = ZoneInfo("America/Los_Angeles")
_LOCK = threading.Lock()

MIRROR_DIR = "/workspace/state/kalshi/ledger"
MIRROR_LEDGER = f"{MIRROR_DIR}/decisions.jsonl"

_ACTIONS = frozenset({"place", "skip", "cancel", "exit", "amend", "outcome", "fill"})
_SIDES = frozenset({"yes", "no"})
_CATEGORIES = frozenset({"sports", "weather", "politics", "crypto", "macro", "other"})
_RANK = frozenset({"find_best_bets", "fe_manual"})
_RESULTS = frozenset({"win", "loss", "void", "partial"})
_EXITS = frozenset({"settlement", "EXIT_EDGE_GONE", "TAKE_PROFIT_MID", "manual"})
_FLB = frozenset({"<10¢", "10–25¢", "25–75¢", "75–90¢", "≥90¢", ">=90¢"})
_FLB_CANON = {">=90¢": "≥90¢"}

_ID_RE = re.compile(r"^\d{8}-\d{4,6}-.+$")
_GROUP_RE = re.compile(r"^[a-z][a-z0-9_]{2,48}$")
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")
_DECISION_ACTIONS = frozenset({"place", "skip", "cancel", "exit", "amend"})

_EXTERNAL_PRIOR = (
    "Published Kalshi-native priors (Bürgi maker/taker, fee dome) are external. "
    "They are not this sleeve's track record."
)


def mirror_ledger_path() -> str:
    return MIRROR_LEDGER


def state_root() -> Path:
    raw = os.environ.get("KALSHI_STATE_DIR")
    if raw is not None and str(raw).strip():
        return Path(str(raw).strip()).expanduser()
    return Path.home() / ".local" / "state" / "kalshi"


def ledger_path() -> Path:
    override = os.environ.get("KALSHI_LEDGER_PATH")
    if override is not None and str(override).strip():
        return Path(str(override).strip()).expanduser()
    return state_root() / "ledger" / "decisions.jsonl"


def _stats_dir() -> Path:
    return ledger_path().parent


def _reject_secrets(text: str) -> None:
    if "PRIVATE KEY" in text or "-----BEGIN" in text:
        raise RuntimeError("decision record was refused")


def _week_key(moment: datetime) -> str:
    iso = moment.astimezone(_PT).isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field} must be an ISO timestamp with a timezone offset")
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise RuntimeError(f"{field} must be an ISO timestamp with a timezone offset") from None
    if parsed.tzinfo is None:
        raise RuntimeError(f"{field} must include a timezone offset (Pacific Time expected)")
    return parsed


def _string(value: object, field: str, *, max_len: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field} is required")
    text = value.strip()
    if len(text) > max_len:
        raise RuntimeError(f"{field} is too long")
    _reject_secrets(text)
    return text


def _optional_string(record: dict, field: str, *, max_len: int) -> str | None:
    if field not in record or record[field] is None:
        return None
    return _string(record[field], field, max_len=max_len)


def _number(value: object, field: str, *, low: Decimal | None = None, high: Decimal | None = None) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise RuntimeError(f"{field} must be a number")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise RuntimeError(f"{field} must be a number") from None
    if not amount.is_finite():
        raise RuntimeError(f"{field} must be a number")
    if low is not None and amount < low:
        raise RuntimeError(f"{field} is below {low}")
    if high is not None and amount > high:
        raise RuntimeError(f"{field} is above {high}")
    return amount


def _optional_number(record: dict, field: str, *, low: Decimal | None = None, high: Decimal | None = None) -> Decimal | None:
    if field not in record or record[field] is None:
        return None
    return _number(record[field], field, low=low, high=high)


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RuntimeError(f"{field} must be a list of strings")
    items = [item.strip() for item in value]
    for item in items:
        _reject_secrets(item)
        if len(item) > 200:
            raise RuntimeError(f"{field} has an entry that is too long")
    return items


def _read_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    text = path.read_text(encoding="utf-8")
    for index, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        _reject_secrets(line)
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            raise RuntimeError(f"ledger line {index} is not valid JSON") from None
        if not isinstance(row, dict):
            raise RuntimeError(f"ledger line {index} must be an object")
        rows.append(row)
    return rows


def _known_ids(rows: list[dict]) -> set[str]:
    found: set[str] = set()
    for row in rows:
        row_id = row.get("id")
        if isinstance(row_id, str):
            found.add(row_id)
    return found


def validate_record(record: object, *, existing_ids: set[str] | None = None) -> dict:
    """Return a copy that is safe to append. Raises RuntimeError on a bad record."""
    if not isinstance(record, dict):
        raise RuntimeError("decision record must be an object")
    action = record.get("action")
    if action not in _ACTIONS:
        raise RuntimeError("action must be place, skip, cancel, exit, amend, outcome, or fill")
    row_id = record.get("id")
    if not isinstance(row_id, str) or _ID_RE.fullmatch(row_id) is None or len(row_id) > 180:
        raise RuntimeError("id must look like YYYYMMDD-HHMMSS-ticker-side-seq")
    _reject_secrets(row_id)
    if existing_ids is not None and action != "outcome" and row_id in existing_ids:
        raise RuntimeError("id already exists; append a new line with parent_id instead of rewriting history")
    _parse_time(record.get("as_of"), "as_of")
    out = dict(record)
    out["action"] = action
    out["id"] = row_id
    if action in {"place", "skip"}:
        _validate_decision(out)
    elif action == "outcome":
        _validate_outcome(out)
    elif action == "fill":
        _validate_fill(out)
    else:
        _string(out.get("ticker"), "ticker", max_len=128)
        if action == "cancel":
            _string(out.get("order_id"), "order_id", max_len=128)
    return out


def _validate_decision(row: dict) -> None:
    action = row["action"]
    ticker = _string(row.get("ticker"), "ticker", max_len=128)
    side = row.get("side")
    if side not in _SIDES:
        raise RuntimeError("side must be yes or no")
    if f"-{side}-" not in row["id"] or ticker not in row["id"]:
        raise RuntimeError("id must contain the ticker and the side")
    group = row.get("corr_group")
    if not isinstance(group, str) or _GROUP_RE.fullmatch(group) is None:
        raise RuntimeError("corr_group must be a snake_case risk driver")
    if row.get("category") not in _CATEGORIES:
        raise RuntimeError("category must be sports, weather, politics, crypto, macro, or other")
    _number(row.get("belief_conf"), "belief_conf", low=Decimal(0), high=Decimal(1))
    sources = row.get("model_sources")
    if not isinstance(sources, list) or not sources:
        raise RuntimeError("model_sources must be a non-empty list of strings")
    _string_list(sources, "model_sources")
    _string(row.get("evidence_summary"), "evidence_summary", max_len=500)
    _optional_number(row, "p_model", low=Decimal(0), high=Decimal(1))
    for field in ("p_exec", "yes_ask", "no_ask"):
        _optional_number(row, field, low=Decimal(0), high=Decimal(1))
    _optional_number(row, "edge_net_cents", low=Decimal("-100"), high=Decimal("100"))
    _optional_number(row, "fee_cents_est", low=Decimal(0), high=Decimal("10000"))
    if "flb_band" in row and row["flb_band"] is not None and row["flb_band"] not in _FLB:
        raise RuntimeError("flb_band is not a known band")
    _optional_number(row, "days_to_res", low=Decimal(0), high=Decimal("3650"))
    if "maker_flag" in row and row["maker_flag"] is not None and not isinstance(row["maker_flag"], bool):
        raise RuntimeError("maker_flag must be a boolean")
    passed = row.get("gates_passed")
    failed = row.get("gates_failed")
    if not isinstance(passed, list) or not all(isinstance(item, str) and item.strip() for item in passed):
        raise RuntimeError("gates_passed must be a list of strings")
    if not isinstance(failed, list) or not all(isinstance(item, str) and item.strip() for item in failed):
        raise RuntimeError("gates_failed must be a list of strings")
    failed_gate = row.get("failed_gate")
    if action == "skip":
        if not failed:
            raise RuntimeError("a skip needs a non-empty gates_failed")
        if not isinstance(failed_gate, str) or failed_gate not in failed:
            raise RuntimeError("failed_gate must be one of gates_failed")
    else:
        if failed:
            raise RuntimeError("a place has an empty gates_failed")
        if failed_gate not in (None,):
            raise RuntimeError("a place has failed_gate null")
        if row.get("confirm") is not True:
            raise RuntimeError("a place requires confirm true")
        _number(row.get("stake_dollars"), "stake_dollars", low=Decimal("0.01"), high=Decimal("100000"))
        _number(row.get("count"), "count", low=Decimal("0.01"), high=Decimal("1000000"))
        _number(row.get("price"), "price", low=Decimal("0.0001"), high=Decimal("0.9999"))
    path = row.get("logic_path")
    if not isinstance(path, list) or not path or not all(isinstance(item, str) and item.strip() for item in path):
        raise RuntimeError("logic_path must be a non-empty list of gate ids")
    if row.get("rank_source") not in _RANK:
        raise RuntimeError("rank_source must be find_best_bets or fe_manual")
    if "order_id" in row and row["order_id"] is not None:
        _string(row["order_id"], "order_id", max_len=128)
    if "confirm" in row and not isinstance(row["confirm"], bool):
        raise RuntimeError("confirm must be a boolean")
    meta = row.get("find_best_bets_meta")
    if meta is not None and not isinstance(meta, dict):
        raise RuntimeError("find_best_bets_meta must be an object")
    _optional_string(row, "notes", max_len=1000)
    _optional_string(row, "side_exec", max_len=32)


def _validate_outcome(row: dict) -> None:
    _string(row.get("parent_id"), "parent_id", max_len=180)
    if not isinstance(row.get("settled"), bool):
        raise RuntimeError("settled must be a boolean")
    if row.get("result") not in _RESULTS:
        raise RuntimeError("result must be win, loss, void, or partial")
    _number(row.get("pnl_dollars"), "pnl_dollars", low=Decimal("-1000000"), high=Decimal("1000000"))
    if not isinstance(row.get("held_to_res"), bool):
        raise RuntimeError("held_to_res must be a boolean")
    if row.get("exit_reason") not in _EXITS:
        raise RuntimeError("exit_reason must be settlement, EXIT_EDGE_GONE, TAKE_PROFIT_MID, or manual")
    _parse_time(row.get("resolved_at"), "resolved_at")


def _validate_fill(row: dict) -> None:
    _string(row.get("ticker"), "ticker", max_len=128)
    _string(row.get("order_id"), "order_id", max_len=128)
    if "side" in row and row["side"] is not None and row["side"] not in _SIDES:
        raise RuntimeError("side must be yes or no")
    if "parent_id" in row and row["parent_id"] is not None:
        _string(row["parent_id"], "parent_id", max_len=180)
    _optional_number(row, "fee_cents_est", low=Decimal(0), high=Decimal("10000"))
    _optional_number(row, "edge_net_cents", low=Decimal("-100"), high=Decimal("100"))
    _optional_string(row, "notes", max_len=1000)


def append_decision(args: dict | None = None) -> dict:
    """Validate one record and append it. Does not rewrite earlier lines."""
    args = args or {}
    incoming = args.get("record") if isinstance(args.get("record"), dict) else None
    if incoming is None and args.get("action"):
        incoming = {key: value for key, value in args.items() if key != "record"}
    with _LOCK:
        path = ledger_path()
        rows = _read_lines(path)
        clean = validate_record(incoming, existing_ids=_known_ids(rows))
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
        _reject_secrets(line)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        count = len(rows) + 1
    return {
        "appended": True,
        "id": clean["id"],
        "action": clean["action"],
        "path": str(path),
        "mirror_path": MIRROR_LEDGER,
        "lines": count,
    }


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite():
        return None
    return amount


def _money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(Decimal("0.01")), "f")


def _rate(numer: Decimal, denom: Decimal) -> str | None:
    if denom == 0:
        return None
    return format((numer / denom).quantize(Decimal("0.0001")), "f")


def _canon_band(value: object) -> str:
    if not isinstance(value, str) or not value:
        return "unknown"
    return _FLB_CANON.get(value, value)


def _dtr_bucket(value: object) -> str:
    days = _decimal(value)
    if days is None:
        return "unknown"
    if days <= 3:
        return "<=3"
    if days <= 7:
        return "4-7"
    return ">7"


def _maker_bucket(parent: dict | None) -> str:
    if not parent:
        return "unknown"
    if parent.get("maker_flag") is True or parent.get("side_exec") == "maker":
        return "maker"
    if parent.get("maker_flag") is False or parent.get("side_exec") == "taker":
        return "taker"
    return "unknown"


def _when(row: dict, field: str) -> datetime | None:
    raw = row.get(field)
    if not isinstance(raw, str):
        return None
    try:
        return _parse_time(raw, field)
    except RuntimeError:
        return None


def _in_week(row: dict, week: str, *, field: str) -> bool:
    moment = _when(row, field)
    if moment is None:
        return False
    return _week_key(moment) == week


def _claim(n_resolved: int) -> str:
    if n_resolved < 10:
        return "counts_and_pnl_only"
    if n_resolved < 30:
        return "directional_wide_uncertainty"
    return "may_refit_min_edge"


def _empty_bucket() -> dict:
    return {"n_resolved": 0, "n_wins": 0, "n_losses": 0, "pnl_dollars": Decimal(0), "stake_dollars": Decimal(0)}


def _finish_bucket(bucket: dict) -> dict:
    wins = bucket["n_wins"]
    losses = bucket["n_losses"]
    pnl = bucket["pnl_dollars"]
    stake = bucket["stake_dollars"]
    return {
        "n_resolved": bucket["n_resolved"],
        "n_wins": wins,
        "n_losses": losses,
        "hit_rate": _rate(Decimal(wins), Decimal(wins + losses)),
        "pnl_dollars": _money(pnl),
        "stake_dollars": _money(stake),
        "roi_after_fees": _rate(pnl, stake),
    }


def _slice(decisions: list[dict], outcomes: list[dict], parents: dict[str, dict]) -> dict:
    places = [row for row in decisions if row.get("action") == "place"]
    skips = [row for row in decisions if row.get("action") == "skip"]
    latest: dict[str, dict] = {}
    for row in outcomes:
        parent_id = row.get("parent_id")
        if isinstance(parent_id, str):
            latest[parent_id] = row
    resolved = [row for row in latest.values() if row.get("settled") is True]
    wins = sum(1 for row in resolved if row.get("result") == "win")
    losses = sum(1 for row in resolved if row.get("result") == "loss")
    voids = sum(1 for row in resolved if row.get("result") == "void")
    partials = sum(1 for row in resolved if row.get("result") == "partial")
    pnl = Decimal(0)
    stake = Decimal(0)
    bands: dict[str, dict] = defaultdict(_empty_bucket)
    makers: dict[str, dict] = defaultdict(_empty_bucket)
    categories: dict[str, dict] = defaultdict(_empty_bucket)
    dtrs: dict[str, dict] = defaultdict(_empty_bucket)
    calibration: list[dict] = []
    binary: list[tuple[Decimal, int]] = []
    for row in resolved:
        parent = parents.get(row.get("parent_id"))
        row_pnl = _decimal(row.get("pnl_dollars")) or Decimal(0)
        pnl += row_pnl
        row_stake = _decimal(parent.get("stake_dollars")) if parent else None
        if row_stake is not None and row_stake > 0:
            stake += row_stake
        band = _canon_band(parent.get("flb_band") if parent else None)
        role = _maker_bucket(parent)
        category = parent.get("category") if parent and isinstance(parent.get("category"), str) else "unknown"
        bucket = _dtr_bucket(parent.get("days_to_res") if parent else None)
        for table, key in ((bands, band), (makers, role), (categories, category), (dtrs, bucket)):
            table[key]["n_resolved"] += 1
            table[key]["pnl_dollars"] += row_pnl
            if row_stake is not None and row_stake > 0:
                table[key]["stake_dollars"] += row_stake
            if row.get("result") == "win":
                table[key]["n_wins"] += 1
            elif row.get("result") == "loss":
                table[key]["n_losses"] += 1
        if parent and _decimal(parent.get("edge_net_cents")) is not None:
            item = {
                "parent_id": row.get("parent_id"),
                "edge_net_cents_predicted": format(_decimal(parent.get("edge_net_cents")), "f"),
                "pnl_dollars": _money(row_pnl),
                "stake_dollars": _money(row_stake) if row_stake is not None else None,
            }
            if row_stake is not None and row_stake > 0:
                item["pnl_per_stake"] = _rate(row_pnl, row_stake)
            calibration.append(item)
        if parent and row.get("result") in {"win", "loss"}:
            conf = _decimal(parent.get("belief_conf"))
            if conf is not None:
                binary.append((conf, 1 if row.get("result") == "win" else 0))
    reasons: dict[str, int] = defaultdict(int)
    for row in skips:
        gate = row.get("failed_gate")
        if isinstance(gate, str) and gate:
            reasons[gate] += 1
    groups: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for row in places:
        group = row.get("corr_group")
        amount = _decimal(row.get("stake_dollars"))
        if isinstance(group, str) and amount is not None:
            groups[group] += amount
    n_binary = len(binary)
    brier = None
    log_score = None
    brier_reason = None
    if n_binary >= 20:
        err = sum((conf - Decimal(outcome)) ** 2 for conf, outcome in binary)
        brier = format((err / Decimal(n_binary)).quantize(Decimal("0.0001")), "f")
        total = Decimal(0)
        for conf, outcome in binary:
            clipped = min(max(conf, Decimal("0.000001")), Decimal("0.999999"))
            prob = clipped if outcome == 1 else Decimal(1) - clipped
            total += Decimal(str(math.log(float(prob))))
        log_score = format((total / Decimal(n_binary)).quantize(Decimal("0.0001")), "f")
    else:
        brier_reason = "n_binary<20"
    n_resolved = len(resolved)
    max_group = max(groups.values()) if groups else Decimal(0)
    return {
        "n_decisions": len(decisions),
        "n_places": len(places),
        "n_skips": len(skips),
        "n_resolved": n_resolved,
        "n_wins": wins,
        "n_losses": losses,
        "n_void": voids,
        "n_partial": partials,
        "hit_rate": _rate(Decimal(wins), Decimal(wins + losses)),
        "pnl_dollars": _money(pnl) if resolved else None,
        "stake_dollars": _money(stake) if resolved else None,
        "roi_after_fees": _rate(pnl, stake) if resolved else None,
        "by_flb_band": {key: _finish_bucket(value) for key, value in sorted(bands.items())},
        "by_maker": {key: _finish_bucket(value) for key, value in sorted(makers.items())},
        "by_category": {key: _finish_bucket(value) for key, value in sorted(categories.items())},
        "by_days_to_res": {key: _finish_bucket(value) for key, value in sorted(dtrs.items())},
        "skip_reasons": dict(sorted(reasons.items())),
        "edge_net_realized_vs_predicted": calibration,
        "n_binary": n_binary,
        "brier": brier,
        "log_score": log_score,
        "brier_reason": brier_reason,
        "claim_level": _claim(n_resolved),
        "external_priors": _EXTERNAL_PRIOR if n_resolved < 30 else None,
        "corr_group_stake_dollars": {key: _money(value) for key, value in sorted(groups.items())},
        "max_group_stake_dollars": _money(max_group) if groups else None,
        "max_concurrent_pct": None,
        "max_concurrent_note": "sleeve cash is not on the ledger, so concurrent percent is not computed",
    }


def _parents(rows: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        if row.get("action") in _DECISION_ACTIONS and isinstance(row.get("id"), str):
            found[row["id"]] = row
    return found


def summarize_decisions(args: dict | None = None) -> dict:
    """Week and life-to-date slices. Counts come only from ledger lines."""
    args = args or {}
    week = args.get("week")
    if week in (None, ""):
        week = _week_key(datetime.now(_PT))
    if not isinstance(week, str) or _WEEK_RE.fullmatch(week) is None:
        raise RuntimeError("week must be YYYY-Www")
    write_stats = args.get("write_stats", True)
    if not isinstance(write_stats, bool):
        raise RuntimeError("write_stats must be a boolean")
    path = ledger_path()
    with _LOCK:
        rows = _read_lines(path)
    parents = _parents(rows)
    decisions = [row for row in rows if row.get("action") in _DECISION_ACTIONS]
    outcomes = [row for row in rows if row.get("action") == "outcome"]
    fills = [row for row in rows if row.get("action") == "fill"]
    week_decisions = [row for row in decisions if _in_week(row, week, field="as_of")]
    week_outcomes = [row for row in outcomes if _in_week(row, week, field="resolved_at")]
    payload = {
        "ledger_path": str(path),
        "mirror_path": MIRROR_LEDGER,
        "lines": len(rows),
        "n_fills": len(fills),
        "week_id": week,
        "week": _slice(week_decisions, week_outcomes, parents),
        "ltd": _slice(decisions, outcomes, parents),
        "sample_rule": {
            "n_resolved_lt_10": "counts and P&L only",
            "n_resolved_10_to_29": "directional notes, wide uncertainty",
            "n_resolved_gte_30": "may re-fit MIN_EDGE; still cite N",
        },
    }
    payload["week"]["n_fills"] = sum(1 for row in fills if _in_week(row, week, field="as_of"))
    payload["ltd"]["n_fills"] = len(fills)
    if write_stats:
        written = _write_stats(week, payload)
        payload["stats_weekly_path"] = written["weekly"]
        payload["stats_ltd_path"] = written["ltd"]
        payload["stats_weekly_mirror"] = f"{MIRROR_DIR}/stats-weekly-{week}.json"
        payload["stats_ltd_mirror"] = f"{MIRROR_DIR}/stats-ltd.json"
    return payload


def _write_stats(week: str, payload: dict) -> dict:
    folder = _stats_dir()
    folder.mkdir(parents=True, exist_ok=True)
    weekly = folder / f"stats-weekly-{week}.json"
    ltd = folder / "stats-ltd.json"
    weekly.write_text(json.dumps({"week_id": week, "week": payload["week"]}, indent=2) + "\n", encoding="utf-8")
    ltd.write_text(
        json.dumps({"lines": payload["lines"], "n_fills": payload["n_fills"], "ltd": payload["ltd"]}, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"weekly": str(weekly), "ltd": str(ltd)}


def record_fill(fields: dict) -> dict:
    """Append one fill ack. Used by place_order when the response shows a fill."""
    return append_decision({"record": fields})


APPEND_DECISION_TOOL = {
    "name": "append_decision",
    "description": (
        "Validate one Kalshi decision or outcome and append it to the JSONL ledger. "
        "Does not place, cancel, or amend an order. Does not rewrite earlier lines. "
        "A settlement or exit is a new line with action outcome and parent_id. "
        "Writes under plugin state (KALSHI_STATE_DIR, default ~/.local/state/kalshi/ledger/decisions.jsonl). "
        "Finance Engineer mirrors that file to /workspace/state/kalshi/ledger/decisions.jsonl. "
        "place and skip need id, as_of (ISO with timezone, Pacific Time expected), ticker, side, "
        "corr_group, category, belief_conf, model_sources, evidence_summary, gates_passed, gates_failed, "
        "failed_gate, logic_path, and rank_source. A place also needs confirm true, stake_dollars, count, and price."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "record": {
                "type": "object",
                "description": "One decision, outcome, or fill object. Appended as one JSON line after validation.",
            },
        },
        "required": ["record"],
    },
}

SUMMARIZE_DECISIONS_TOOL = {
    "name": "summarize_decisions",
    "description": (
        "Week and life-to-date stats from the decision ledger only. "
        "Does not place orders and does not invent N. "
        "hit_rate is wins divided by wins+losses. void and partial stay in n_resolved and out of that ratio. "
        "roi_after_fees is realized pnl divided by the parent place stake. "
        "Brier and log-score are omitted until 20 binary outcomes. "
        "N_resolved under 10 is counts and P&L only. "
        "External maker/taker priors are labeled as external when own N is under 30. "
        "week is YYYY-Www in Pacific Time. Default is the current Pacific week. "
        "Regenerates stats-weekly-YYYY-Www.json and stats-ltd.json next to the ledger unless write_stats is false."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "week": {"type": "string", "description": "ISO week YYYY-Www in Pacific Time. Default is the current Pacific week."},
            "write_stats": {
                "type": "boolean",
                "description": "Rewrite the rolling stats JSON files. Default true. Does not rewrite decisions.jsonl.",
            },
        },
    },
}
