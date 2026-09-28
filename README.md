# kalshi-readonly

Read-only Kalshi MCP for Trade API v2. It exposes exchange status, markets, and signed cash and positions over stdio.

Read-only v1 / no trading. There is no place, cancel, buy, sell, deposit, or withdraw tool.

## Run

Python 3.11+ and `cryptography==41.0.7` (see `requirements.txt`).

```bash
pip install -r requirements.txt
python3 server.py
```

`server.py` speaks MCP on stdin/stdout and waits for a host. Do not type at it.

```bash
pip install -r requirements-dev.txt
pytest
```

## Environment

Copy `.env.example`. Portfolio calls need a key id and a private key. Public market calls do not.

| Variable | Required | Purpose |
| --- | --- | --- |
| `KALSHI_API_KEY_ID` | portfolio calls | API key id |
| `KALSHI_PRIVATE_KEY_PATH` | one of these | PEM file path |
| `KALSHI_PRIVATE_KEY_PEM` | one of these | PEM contents |
| `KALSHI_API_BASE` | no | Default `https://api.elections.kalshi.com/trade-api/v2` |

Missing auth fails before any portfolio request. The private key is used only to sign. It is not logged and not returned. If a response or error would contain key material, that text is dropped.

Signing matches the cash CLI: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Prove

Point the env vars at the key id file and the PEM. This prints one balance read and does not place orders.

```bash
export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < /path/to/key_id)"
export KALSHI_PRIVATE_KEY_PATH=/path/to/private.pem
python3 -c 'import json; from kalshi_readonly.tools import cash_or_positions; print(json.dumps(cash_or_positions({"include":"both","limit":5}), indent=2))'
```

## Cursor

```json
{
  "mcpServers": {
    "kalshi-readonly": {
      "command": "python3",
      "args": ["/absolute/path/to/kalshi-mcp/server.py"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/private.pem"
      }
    }
  }
}
```

`mcp.json` in this repo is the same launch for a plugin host (`${PLUGIN_ROOT}/server.py`).

## Grok Bot

AddMcpServer for a stdio server named `kalshi-readonly`. Command `python3`. Args are the absolute path of `server.py`. Set `KALSHI_API_KEY_ID` from the key id file and `KALSHI_PRIVATE_KEY_PATH` to the PEM path in that server's env. Do not paste the private key into chat.

## Tools

| Tool | What it reads |
| --- | --- |
| `exchange_status` | Public exchange status |
| `list_markets` | Public markets. Optional `limit` (default 5), `status`, `ticker` |
| `cash_or_positions` | Cash and positions. `include`: `balance`, `cash`, `positions`, `both` (default), or `fills`. Optional `limit` (default 50) |

`cash_or_positions` returns `balance_cents`, `balance_dollars`, and `cash` (the same official dollar string). `portfolio_value` is included only when the balance endpoint sends it. `market_positions` and `event_positions` each include `ticker` and `qty` when the API sends a quantity. Market `side` is the official sign of that quantity: positive YES, negative NO. `avg` and `mark` are copied only when the API sends them. `include=fills` reads recent fills (ticker, side, qty, official prices). This server does not compute remaining-to-recover.
