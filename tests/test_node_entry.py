"""Node stdio entry lists reads and refuses a mutation while safe mode is on."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "dist" / "index.js"

pytestmark = pytest.mark.skipif(not ENTRY.is_file() or shutil.which("node") is None, reason="node entry is not built")

_INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"},
    },
}


def _run(safe_mode: str, extra: list[dict] | None = None) -> tuple[list[dict], str]:
    env = os.environ.copy()
    env["KALSHI_SAFE_MODE"] = safe_mode
    for key in (
        "KALSHI_API_KEY_ID",
        "KALSHI_API_KEY_ID_PATH",
        "KALSHI_PRIVATE_KEY_PEM",
        "KALSHI_PRIVATE_KEY_PATH",
    ):
        env.pop(key, None)
    messages = [
        _INIT,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        *(extra or []),
    ]
    proc = subprocess.run(
        ["node", str(ENTRY)],
        input="".join(json.dumps(message) + "\n" for message in messages),
        text=True,
        capture_output=True,
        timeout=20,
        env=env,
        cwd=ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return rows, proc.stderr


def _status_line(stderr: str) -> str:
    return next(line for line in stderr.splitlines() if line.startswith("kalshi-readonly node "))


def test_node_stdio_lists_find_best_bets_and_refuses_place() -> None:
    rows, stderr = _run(
        "1",
        [
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "find_best_bets",
                    "arguments": {"beliefs": [{"ticker": "KXTEST", "side": "yes", "confidence": 2}]},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "place_order", "arguments": {"confirm": True}},
            },
        ],
    )
    assert [row["id"] for row in rows] == [1, 2, 3, 4]
    names = [tool["name"] for tool in rows[1]["result"]["tools"]]
    assert "find_best_bets" in names
    assert "fe_routine" in names
    assert "exchange_status" in names
    assert "place_order" not in names
    assert rows[2]["result"]["isError"] is True
    assert "confidence must be greater than 0" in rows[2]["result"]["content"][0]["text"]
    assert "KALSHI_SAFE_MODE is on" in rows[3]["result"]["content"][0]["text"]
    assert "PRIVATE KEY" not in "\n".join(json.dumps(row) for row in rows)
    status = _status_line(stderr)
    assert "safeMode=on" in status
    assert "fe_routine" in status
    assert "place_order" not in status


def test_node_stdio_lists_trade_tools_when_safe_mode_is_off() -> None:
    rows, stderr = _run("0")
    assert [row["id"] for row in rows] == [1, 2]
    names = [tool["name"] for tool in rows[1]["result"]["tools"]]
    for name in (
        "exchange_status",
        "find_best_bets",
        "fe_routine",
        "place_order",
        "cancel_order",
        "amend_order",
        "decrease_order",
    ):
        assert name in names
    status = _status_line(stderr)
    assert "safeMode=off" in status
    for name in ("fe_routine", "place_order", "cancel_order", "amend_order", "decrease_order"):
        assert name in status
