"""Minimal MCP stdio loop. stdout is protocol only."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

PROTOCOL = "2024-11-05"


def public_error(exc: BaseException) -> str:
    text = str(exc)
    if "PRIVATE KEY" in text or "-----BEGIN" in text:
        return "Kalshi request failed"
    return text


def _read_message() -> dict | None:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    if line.strip().startswith(b"{"):
        return json.loads(line.decode("utf-8"))
    headers: dict[str, str] = {}
    key, _, value = line.decode("utf-8").partition(":")
    headers[key.strip().lower()] = value.strip()
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        key, _, value = line.decode("utf-8").partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    if length <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def _write_message(msg: dict) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def run_server(
    name: str,
    version: str,
    tools: list[dict] | Callable[[], list[dict]],
    handlers: dict[str, Callable[[dict], Any]],
) -> None:
    while True:
        msg = _read_message()
        if msg is None:
            return
        mid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if method == "initialize":
            _write_message(
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "protocolVersion": PROTOCOL,
                        "capabilities": {"tools": {"listChanged": False}},
                        "serverInfo": {"name": name, "version": version},
                    },
                }
            )
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            listed = tools() if callable(tools) else tools
            _write_message({"jsonrpc": "2.0", "id": mid, "result": {"tools": listed}})
        elif method == "tools/call":
            try:
                handler = handlers.get(params.get("name"))
                if handler is None:
                    raise RuntimeError("unknown tool")
                out = handler(params.get("arguments") or {})
                text = out if isinstance(out, str) else json.dumps(out, indent=2)
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {"content": [{"type": "text", "text": text}], "isError": False},
                    }
                )
            except Exception as exc:
                _write_message(
                    {
                        "jsonrpc": "2.0",
                        "id": mid,
                        "result": {
                            "content": [{"type": "text", "text": f"error: {public_error(exc)}"}],
                            "isError": True,
                        },
                    }
                )
        elif method == "ping":
            _write_message({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif mid is not None:
            _write_message(
                {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
            )
