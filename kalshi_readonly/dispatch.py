"""JSON-line worker for the Node stdio process. stdout is protocol only."""

from __future__ import annotations

import json
import sys

from kalshi_readonly.stdio import public_error
from kalshi_readonly.tools import HANDLERS, registered_tools


def handle(msg: object) -> dict:
    if not isinstance(msg, dict):
        return {"id": None, "ok": False, "error": "invalid message"}
    mid = msg.get("id")
    op = msg.get("op")
    try:
        if op == "list":
            return {"id": mid, "ok": True, "tools": registered_tools()}
        if op == "call":
            name = msg.get("name")
            handler = HANDLERS.get(name) if isinstance(name, str) else None
            if handler is None:
                raise RuntimeError("unknown tool")
            args = msg.get("arguments") or {}
            if not isinstance(args, dict):
                raise RuntimeError("arguments must be an object")
            return {"id": mid, "ok": True, "result": handler(args)}
        raise RuntimeError("unknown op")
    except Exception as exc:
        return {"id": mid, "ok": False, "error": public_error(exc)}


def _write(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main() -> None:
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            _write({"id": None, "ok": False, "error": "invalid json"})
            continue
        _write(handle(msg))


if __name__ == "__main__":
    main()
