# kalshi-readonly

Read-only Kalshi MCP for Trade API v2. Public exchange status and markets, plus signed cash and positions.

Read-only v1 / no trading. There is no place, cancel, buy, sell, deposit, or withdraw tool.

## Run

```bash
python3 server.py
```

Requires Python 3.11+ and the `cryptography` package. The process speaks MCP on stdin/stdout. `plugin.json` and `mcp.json` are the plugin manifest; hosts launch `${PLUGIN_ROOT}/server.py`.

```bash
python3 -m unittest tests.test_readonly
```

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `KALSHI_API_KEY_ID` | portfolio calls | API key id |
| `KALSHI_PRIVATE_KEY_PATH` | one of these | PEM file path |
| `KALSHI_PRIVATE_KEY_PEM` | one of these | PEM contents |
| `KALSHI_API_BASE` | no | Default `https://api.elections.kalshi.com/trade-api/v2` |

`exchange_status` and `list_markets` are public. Cash and positions need the key. The private key is used only to sign requests. It is not logged or returned.

Signing matches the cash CLI: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Tools

| Tool | What it reads |
| --- | --- |
| `exchange_status` | Public exchange status |
| `list_markets` | Public markets. Optional `limit` (default 5), `status`, `ticker` |
| `cash_or_positions` | Cash and positions. `include`: `balance`, `cash`, `positions`, `both` (default), or `fills`. Optional `limit` (default 50) |

`cash` is the official `balance_dollars` string. `portfolio_value` is included when the balance endpoint returns it. Positions keep market and event rows. `qty` aliases the official quantity (`position_fp` or `position`, or the event share count). `side`, `avg`, and `mark` are copied only when the API sends them. This server does not compute remaining-to-recover.
