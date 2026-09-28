"""server.py stays importable when Python does not put the script directory on sys.path."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _frames(raw: bytes) -> list[dict]:
    view = io.BytesIO(raw)
    found: list[dict] = []
    while True:
        line = view.readline()
        if not line:
            break
        if not line.lower().startswith(b"content-length:"):
            continue
        length = int(line.split(b":", 1)[1].strip())
        assert view.readline() in (b"\r\n", b"\n")
        found.append(json.loads(view.read(length)))
    return found


def test_server_import_survives_safepath(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("KALSHI_SAFE_MODE", None)
    env["PYTHONSAFEPATH"] = "1"
    message = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    proc = subprocess.run(
        [sys.executable, "-P", str(ROOT / "server.py")],
        input=json.dumps(message) + "\n",
        text=True,
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    frames = _frames(proc.stdout.encode())
    assert [frame["id"] for frame in frames] == [1]
    names = [tool["name"] for tool in frames[0]["result"]["tools"]]
    assert "find_best_bets" in names
    assert "place_order" not in names
    assert "PRIVATE KEY" not in proc.stdout
