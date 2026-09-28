#!/usr/bin/env python3
"""Kalshi read-only MCP — public markets + auth-gated cash/positions."""
from __future__ import annotations
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    Path("/tmp/mcp-kalshi-readonly-boot.log").write_text(f"boot {time.time()} {__file__}\n")
except Exception:
    pass

PROTOCOL = "2024-11-05"

def _read_message() -> Optional[dict]:
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    stripped = line.strip()
    if stripped.startswith(b"{"):
        return json.loads(stripped.decode("utf-8"))
    headers: Dict[str, str] = {}
    k, _, v = line.decode("utf-8").partition(":")
    headers[k.strip().lower()] = v.strip()
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        k, _, v = line.decode("utf-8").partition(":")
        headers[k.strip().lower()] = v.strip()
    n = int(headers.get("content-length", "0"))
    if n <= 0:
        return None
    return json.loads(sys.stdin.buffer.read(n).decode("utf-8"))

def _write_message(msg: dict) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()

def run_server(name: str, version: str, tools: List[dict], handlers: Dict[str, Callable[[dict], Any]]) -> None:
    while True:
        msg = _read_message()
        if msg is None:
            return
        mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
        if method == "initialize":
            _write_message({"jsonrpc":"2.0","id":mid,"result":{"protocolVersion":PROTOCOL,"capabilities":{"tools":{"listChanged":False}},"serverInfo":{"name":name,"version":version}}})
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            _write_message({"jsonrpc":"2.0","id":mid,"result":{"tools":tools}})
        elif method == "tools/call":
            try:
                out = handlers[params.get("name")](params.get("arguments") or {})
                text = out if isinstance(out,str) else json.dumps(out, indent=2)
                _write_message({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":text}],"isError":False}})
            except Exception as e:
                _write_message({"jsonrpc":"2.0","id":mid,"result":{"content":[{"type":"text","text":f"error: {e}"}],"isError":True}})
        elif method == "ping":
            _write_message({"jsonrpc":"2.0","id":mid,"result":{}})
        elif mid is not None:
            _write_message({"jsonrpc":"2.0","id":mid,"error":{"code":-32601,"message":f"Method not found: {method}"}})

BASE = os.environ.get("KALSHI_API_BASE", "https://api.elections.kalshi.com/trade-api/v2")

def _public_get(path: str, query: dict | None = None) -> dict:
    qs = f"?{urllib.parse.urlencode(query)}" if query else ""
    req = urllib.request.Request(f"{BASE}{path}{qs}", headers={"User-Agent":"tinkabot-kalshi-mcp/0.1","Accept":"application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def _load_auth() -> tuple[str, str]:
    key_id = os.environ.get("KALSHI_API_KEY_ID")
    pem = os.environ.get("KALSHI_PRIVATE_KEY_PEM")
    pem_path = os.environ.get("KALSHI_PRIVATE_KEY_PATH")
    if not pem and pem_path:
        pem = Path(pem_path).read_text(encoding="utf-8")
    if not key_id or not pem:
        raise RuntimeError("auth required: set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH (or PEM)")
    return key_id, pem

def _signature(pem: str, ts: str, method: str, path: str) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    path_without_query = path.split("?", 1)[0]
    private_key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    sig = private_key.sign(
        f"{ts}{method.upper()}{path_without_query}".encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return base64.b64encode(sig).decode("ascii")

def _sign_headers(method: str, path: str) -> dict:
    key_id, pem = _load_auth()
    ts = str(int(time.time() * 1000))
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": ts,
        "KALSHI-ACCESS-SIGNATURE": _signature(pem, ts, method, path),
        "User-Agent": "tinkabot-kalshi-mcp/0.1",
        "Accept": "application/json",
    }

def _signed_path(path: str) -> str:
    return urllib.parse.urlparse(BASE + path).path

def _auth_get(path: str, query: dict | None = None) -> dict:
    qs = f"?{urllib.parse.urlencode(query)}" if query else ""
    full_path = _signed_path(path)
    headers = _sign_headers("GET", full_path)
    req = urllib.request.Request(f"{BASE}{path}{qs}", headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))

def _with_cash(balance: dict) -> dict:
    """Keep the official balance object. `cash` aliases `balance_dollars`."""
    if not isinstance(balance, dict):
        return balance
    out = dict(balance)
    dollars = out.get("balance_dollars")
    if isinstance(dollars, str) and "cash" not in out:
        out["cash"] = dollars
    return out

_QTY_KEYS = ("position_fp", "position", "count_fp", "count", "total_cost_shares_fp", "total_cost_shares")
_AVG_KEYS = ("average_price_dollars", "average_price", "avg_price_dollars", "avg_price")
_MARK_KEYS = ("mark_price_dollars", "mark_price", "mark")

def _alias_row(row: dict) -> dict:
    """Copy official fields. Add qty/side/avg/mark only from fields the API sent."""
    if not isinstance(row, dict):
        return row
    out = dict(row)
    if "ticker" not in out and out.get("event_ticker"):
        out["ticker"] = out["event_ticker"]
    elif "ticker" not in out and out.get("market_ticker"):
        out["ticker"] = out["market_ticker"]
    if "qty" not in out:
        for key in _QTY_KEYS:
            if key in out:
                out["qty"] = out[key]
                break
    if "side" not in out and out.get("outcome_side"):
        out["side"] = out["outcome_side"]
    if "avg" not in out:
        for key in _AVG_KEYS:
            if key in out:
                out["avg"] = out[key]
                break
    if "mark" not in out:
        for key in _MARK_KEYS:
            if key in out:
                out["mark"] = out[key]
                break
    return out

def _alias_list(payload: dict, key: str) -> dict:
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    rows = out.get(key)
    if isinstance(rows, list):
        out[key] = [_alias_row(row) for row in rows]
    return out

def _alias_positions(payload: dict) -> dict:
    out = _alias_list(payload, "market_positions")
    return _alias_list(out, "event_positions")

def exchange_status(_args: dict):
    return _public_get("/exchange/status")

def list_markets(args: dict):
    q = {"limit": int(args.get("limit", 5))}
    if args.get("status"):
        q["status"] = args["status"]
    if args.get("ticker"):
        q["ticker"] = args["ticker"]
    data = _public_get("/markets", q)
    markets = data.get("markets") or []
    compact = [{"ticker":m.get("ticker"),"title":m.get("title"),"status":m.get("status"),"yes_bid":m.get("yes_bid"),"yes_ask":m.get("yes_ask"),"volume":m.get("volume")} for m in markets]
    return {"count": len(compact), "markets": compact, "cursor": data.get("cursor")}

def cash_or_positions(args: dict):
    want = (args.get("include") or "both").lower()
    limit = int(args.get("limit", 50))
    out = {}
    if want in ("balance", "both", "cash"):
        out["balance"] = _with_cash(_auth_get("/portfolio/balance"))
    if want in ("positions", "both"):
        out["positions"] = _alias_positions(_auth_get("/portfolio/positions", {"limit": limit}))
    if want == "fills":
        out["fills"] = _alias_list(_auth_get("/portfolio/fills", {"limit": limit}), "fills")
    return out

TOOLS = [
    {"name":"exchange_status","description":"Public Kalshi exchange status.","inputSchema":{"type":"object","properties":{}}},
    {"name":"list_markets","description":"List Kalshi markets (public).","inputSchema":{"type":"object","properties":{"limit":{"type":"integer"},"status":{"type":"string"},"ticker":{"type":"string"}}}},
    {"name":"cash_or_positions","description":"Read-only Kalshi cash/positions (requires API key env). include: balance, cash, positions, both (default), or fills. Returns official balance_dollars (also as cash), portfolio_value when present, and market plus event positions. qty aliases the official quantity. side, avg, and mark are copied only when the API sends them.","inputSchema":{"type":"object","properties":{"include":{"type":"string"},"limit":{"type":"integer"}}}},
]

def main() -> None:
    run_server("kalshi-readonly", "0.1.0", TOOLS, {"exchange_status":exchange_status,"list_markets":list_markets,"cash_or_positions":cash_or_positions})

if __name__ == "__main__":
    main()
