# kalshi-mcp

Read-only MCP server for Kalshi Trade API v2. It exposes portfolio balance, positions, and fills over stdio.

Read-only v1 / no trading. There is no place, cancel, buy, sell, deposit, or withdraw tool.

## Run

```bash
npm install
npm run build
npm start
```

`npm start` speaks MCP on stdin/stdout. `npm test` checks the signing path without calling Kalshi.

Cursor (or another host) can launch the built server:

```json
{
  "mcpServers": {
    "kalshi": {
      "command": "node",
      "args": ["/absolute/path/to/kalshi-mcp/dist/index.js"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/private.pem"
      }
    }
  }
}
```

## Environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `KALSHI_API_KEY_ID` | yes | API key id |
| `KALSHI_PRIVATE_KEY_PATH` | one of these | PEM file path |
| `KALSHI_PRIVATE_KEY_PEM` | one of these | PEM contents. `\n` escapes are accepted |
| `KALSHI_API_BASE` | no | Default `https://api.elections.kalshi.com/trade-api/v2` |

The private key is used only to sign requests. It is not logged and not returned by tools.

Requests are signed the same way as the Kalshi CLI: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Tools

| Tool | What it reads |
| --- | --- |
| `get_balance` | Available balance in cents and dollars, plus portfolio value |
| `get_positions` | Open positions (non-zero contracts). Optional `limit` (1–200, default 50), `cursor`, `ticker` |
| `get_fills` | Recent fills. Same optional `limit`, `cursor`, and `ticker` |

Non-GET methods are refused in `src/safety.ts` (`ALLOW_WRITES` is false).
