# kalshi-readonly

Kalshi Trade API v2 MCP over stdio. Read tools cover exchange status, markets, cash, and positions. Trade tools place, cancel, amend, or decrease one order, and list resting orders.

Mutating trade tools stay off unless safe mode is turned off. There is no deposit tool, no withdraw tool, and no auto-trading tool.

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

Copy `.env.example`. Portfolio and order calls need a key id and a private key. Public market calls do not.

| Variable | Required | Purpose |
| --- | --- | --- |
| `KALSHI_API_KEY_ID` | portfolio and trade calls | API key id |
| `KALSHI_PRIVATE_KEY_PATH` | one of these | PEM file path |
| `KALSHI_PRIVATE_KEY_PEM` | one of these | PEM contents |
| `KALSHI_API_BASE` | no | Default `https://api.elections.kalshi.com/trade-api/v2` |
| `KALSHI_SAFE_MODE` | no | Defaults to on. See Trade. |

Missing auth fails before any portfolio or order request. The private key is used only to sign. It is not logged and not returned. If a response or error would contain key material, that text is dropped.

Signing matches the cash CLI for GET, POST, and DELETE: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded, body excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Prove

Point the env vars at the key id file and the PEM. This prints one balance read and does not place orders.

```bash
export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < /path/to/key_id)"
export KALSHI_PRIVATE_KEY_PATH=/path/to/private.pem
python3 -c 'import json; from kalshi_readonly.tools import cash_or_positions; print(json.dumps(cash_or_positions({"include":"both","limit":5}), indent=2))'
```

## Install as a Cursor plugin

Cursor hosts read `.cursor-plugin/plugin.json` (`kalshi-readonly`, version `0.2.0`). Root `plugin.json` and `mcp.json` stay in place. Skills are under `skills/`. Stdio launch is root `mcp.json`: command `python3`, args `${PLUGIN_ROOT}/server.py`, env `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, `KALSHI_PRIVATE_KEY_PEM`, and `KALSHI_SAFE_MODE` (default on). No secrets are committed.

This repository is not published to the Cursor Marketplace or cursor.directory. After a publish, install with InstallPlugin. Until then, use the IDE `mcp.json` entry below.

Grok Bot custom stdio AddMcpServer has been unreliable for this server. Prefer InstallPlugin after publish, or the IDE `mcp.json` entry. Do not paste the private key into chat. Do not set `confirm` unless Akash has confirmed that order.

## Cursor

```json
{
  "mcpServers": {
    "kalshi-readonly": {
      "command": "python3",
      "args": ["/absolute/path/to/kalshi-mcp/server.py"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/private.pem",
        "KALSHI_SAFE_MODE": "1"
      }
    }
  }
}
```

`mcp.json` in this repo is the same launch for a plugin host (`${PLUGIN_ROOT}/server.py`).

## Tools

| Tool | What it does |
| --- | --- |
| `exchange_status` | Public exchange status |
| `list_markets` | Public markets. Optional `limit` (default 5), `status`, `ticker` |
| `cash_or_positions` | Cash and positions. `include`: `balance`, `cash`, `positions`, `both` (default), or `fills`. Optional `limit` (default 50) |
| `list_open_orders` | Resting orders only (`status=resting`). Optional `ticker`, `limit` (default 100), `cursor`, `subaccount`. Read. No confirm |
| `place_order` | One order. Hidden until safe mode is off. Requires `confirm: true` |
| `cancel_order` | One cancel. Hidden until safe mode is off. Requires `confirm: true` |
| `amend_order` | Price and total fillable count. Hidden until safe mode is off. Requires `confirm: true` |
| `decrease_order` | Reduce resting size. Hidden until safe mode is off. Requires `confirm: true` |

`cash_or_positions` returns `balance_cents`, `balance_dollars`, and `cash` (the same official dollar string). `portfolio_value` is included only when the balance endpoint sends it. `market_positions` and `event_positions` each include `ticker` and `qty` when the API sends a quantity. Market `side` is the official sign of that quantity: positive YES, negative NO. `avg` and `mark` are copied only when the API sends them. `include=fills` reads recent fills (ticker, side, qty, official prices). This server does not compute remaining-to-recover.

## Trade

Stake guardrail is **confirm_only**. There is no dollar max. The server will not choose a size, chase profit, or set `confirm` for you.

`KALSHI_SAFE_MODE` defaults to on. Missing, empty, `1`, `true`, `yes`, `on`, and any other value keep it on. Only `0`, `false`, `no`, and `off` turn it off. While it is on:

- `place_order`, `cancel_order`, `amend_order`, and `decrease_order` are omitted from `tools/list`.
- Calling one of those handlers anyway returns an error and does not hit the network.
- `list_open_orders` stays registered. It is a signed GET of resting orders.

After you set `KALSHI_SAFE_MODE=0`, restart the MCP host so it reloads the tool list. Every mutating call still requires `confirm` to be the boolean `true`. `false`, `"true"`, `1`, and a missing confirm are refused before auth and before HTTP. The confirm flag is not copied into the Kalshi body.

| Tool | Kalshi route | Required arguments |
| --- | --- | --- |
| `place_order` | `POST /portfolio/events/orders` | `ticker`, `side` (`bid` or `ask`), `count`, `price`, `time_in_force`, `self_trade_prevention_type`, `confirm` |
| `cancel_order` | `DELETE /portfolio/events/orders/{order_id}` | `order_id`, `confirm`. Pass `market_ticker` so Kalshi can auto-route |
| `amend_order` | `POST /portfolio/events/orders/{order_id}/amend` | `order_id`, `ticker`, `side`, `price`, `count`, `confirm` |
| `decrease_order` | `POST /portfolio/events/orders/{order_id}/decrease` | `order_id`, `confirm`, and exactly one of `reduce_by` or `reduce_to` |
| `list_open_orders` | `GET /portfolio/orders?status=resting` | none |

`bid` buys YES. `ask` sells YES. Prices are YES-book dollar strings strictly between 0 and 1, such as `0.4200`. `count` is a contract string such as `1.00` (a positive integer is accepted). Amend `count` is already filled plus the desired resting remainder, not a reduce-by amount. Decreasing size keeps queue position. A price change or a larger size does not. Use `decrease_order` to reduce.

`time_in_force` is `fill_or_kill`, `good_till_canceled`, or `immediate_or_cancel`. `self_trade_prevention_type` is `taker_at_cross` or `maker`. `expiration_time` is only valid with `good_till_canceled`. `reduce_only` is only valid with `immediate_or_cancel`. Pass `client_order_id` yourself when you want dedup. This server does not invent one.

Optional place fields: `client_order_id`, `expiration_time`, `post_only`, `reduce_only`, `cancel_order_on_pause`, `subaccount`, `order_group_id`, `exchange_index`.

Demo host, if you use one: `KALSHI_API_BASE=https://demo-api.kalshi.co/trade-api/v2`.

There is no batch place, no batch cancel, no withdraw, and no deposit.
