# kalshi-readonly

Kalshi MCP for Trade API v2. It exposes exchange status, markets, and signed cash, positions, and resting orders over stdio.

Trade writes exist and stay off until you opt in. With `KALSHI_SAFE_MODE` unset or `1` (the default), `place_order`, `cancel_order`, `amend_order`, and `decrease_order` are omitted from `tools/list` and refused if called. `confirm: true` does not bypass that. There is no deposit tool, no withdraw tool, and no strategy tool.

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
| `KALSHI_SAFE_MODE` | no | Unset or `1` hides trade writes. `0` registers them. They still need `confirm: true` |

Missing auth fails before any portfolio request. The private key is used only to sign. It is not logged and not returned. If a response or error would contain key material, that text is dropped.

Signing matches the cash CLI for GET, POST, and DELETE: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded, body excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Safe mode

`KALSHI_SAFE_MODE` defaults to **on** when unset, same pattern as bambu-mcp. Only `0`, `false`, `off`, and `no` turn it off. Anything else, including `1` or an empty expansion, keeps it on.

While it is on, the server lists and runs only reads:

| Tool | What it does |
| --- | --- |
| `exchange_status` `list_markets` | public reads |
| `cash_or_positions` `list_open_orders` | signed reads |

`place_order`, `cancel_order`, `amend_order`, and `decrease_order` are left out of that catalog. They come back when you set `KALSHI_SAFE_MODE=0` and restart the server. The gate still names that variable. `confirm: true` does not unlock safe mode.

Set `KALSHI_SAFE_MODE=0` yourself, in the shell or the MCP `env` block. An agent must not change it.

After that, every trade write still requires `confirm: true`. Ask first. The server never sets `confirm`. A missing confirm, `false`, `"true"`, or `1` is a refusal and does not call Kalshi. There is no dollar cap in this server: you confirm the exact order.

The server stays read-only until both are true: `KALSHI_SAFE_MODE=0` and the call includes `confirm: true`.

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
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/private.pem",
        "KALSHI_SAFE_MODE": "1"
      }
    }
  }
}
```

`mcp.json` in this repo is the plugin launch (`${PLUGIN_ROOT}/server.py`). It passes `KALSHI_SAFE_MODE` through. An unset value stays safe.

## Grok Bot

AddMcpServer for a stdio server named `kalshi-readonly`. Command `python3`. Args are the absolute path of `server.py`. Set `KALSHI_API_KEY_ID` from the key id file and `KALSHI_PRIVATE_KEY_PATH` to the PEM path in that server's env. Leave `KALSHI_SAFE_MODE` unset or set it to `1`. Do not paste the private key into chat. Do not flip safe mode and do not set `confirm` unless the operator asked for that exact order.

## Tools

| Tool | Safe mode on | Confirm | What it does |
| --- | --- | --- | --- |
| `exchange_status` | allowed | no | Public exchange status |
| `list_markets` | allowed | no | Public markets. Optional `limit` (default 5), `status`, `ticker` |
| `cash_or_positions` | allowed | no | Cash and positions. `include`: `balance`, `cash`, `positions`, `both` (default), or `fills`. Optional `limit` (default 50) |
| `list_open_orders` | allowed | no | Resting orders (`GET /portfolio/orders?status=resting`). Optional `ticker`, `limit` (default 50), `cursor` |
| `place_order` | hidden and refused | **yes** | `POST /portfolio/events/orders` |
| `cancel_order` | hidden and refused | **yes** | `DELETE /portfolio/events/orders/{order_id}` |
| `amend_order` | hidden and refused | **yes** | `POST /portfolio/events/orders/{order_id}/amend` |
| `decrease_order` | hidden and refused | **yes** | `POST /portfolio/events/orders/{order_id}/decrease` |

`cash_or_positions` returns `balance_cents`, `balance_dollars`, and `cash` (the same official dollar string). `portfolio_value` is included only when the balance endpoint sends it. `market_positions` and `event_positions` each include `ticker` and `qty` when the API sends a quantity. Market `side` is the official sign of that quantity: positive YES, negative NO. `avg` and `mark` are copied only when the API sends them. `include=fills` reads recent fills (ticker, side, qty, official prices). This server does not compute remaining-to-recover.

## Trade tools

These four tools are the only writes. They are hidden until `KALSHI_SAFE_MODE=0`, and each call still needs `confirm: true`. The server does not choose a side, size, or price for you. It does not loop, chase profit, or send a follow-up order.

`place_order` posts one V2 event order. `side` is `bid` (buy YES) or `ask` (sell YES). `price` is a fixed-point dollar string with 2–4 decimals, such as `"0.5600"`, and must be greater than 0 and less than 1. `count` is a contract count (`"1"` or `1`). `time_in_force` defaults to `good_till_canceled`. `self_trade_prevention_type` defaults to `taker_at_cross`. Pass `client_order_id` to dedupe a retry; otherwise the server generates one UUID for that call. `reduce_only` is sent only with `immediate_or_cancel`. `confirm` is not sent to Kalshi.

`cancel_order` needs `order_id` and `ticker`. The ticker is the `market_ticker` query used to route the delete. The signature covers the path only.

`amend_order` needs `order_id`, `ticker`, `side`, `price`, and `count`. `count` is the updated total fillable size: contracts already filled plus the resting size you want left. A smaller size keeps queue position. A new price or a larger size goes to the back of the queue.

`decrease_order` needs `order_id` and exactly one of `reduce_by` or `reduce_to`. `reduce_to` may be `0`. Optional `ticker` is sent as `market_ticker` for routing.

`list_open_orders` is a read. It stays listed while safe mode is on and does not take `confirm`.

There is no batch place, no cancel-all, no deposit, and no withdraw.
