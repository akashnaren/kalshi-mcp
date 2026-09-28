# kalshi-readonly

Kalshi Trade API v2 MCP over stdio. Read tools cover exchange status, markets, cash, positions, and a ranked list of small-stake ideas. Trade tools place, cancel, amend, or decrease one order, and list resting orders.

House default is read-only. Mutating trade tools stay off unless safe mode is turned off. There is no deposit tool, no withdraw tool, and no auto-trading tool. `find_best_bets` never places an order.

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

Cursor hosts read `.cursor-plugin/plugin.json` (`kalshi-readonly`, version `0.3.0`). Root `plugin.json` and `mcp.json` stay in place. Skills are under `skills/`. The Finance Engineer checklist is `harness/SKILL.md`. Stdio launch for the Cursor plugin is root `mcp.json`: command `python3`, args `${PLUGIN_ROOT}/server.py`, env `KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`, `KALSHI_PRIVATE_KEY_PEM`, and `KALSHI_SAFE_MODE` (default on). No secrets are committed.

This repository is not published to the Cursor Marketplace or cursor.directory. Publishing is out of scope. Until then, use the IDE `mcp.json` entry below, or the Grok Bot command in the next section.

`server.py` puts its own directory on `sys.path` before importing the package, so a host can launch it from another working directory. That includes Python's safe path (`python3 -P`). Do not paste the private key into chat. Do not set `confirm` unless Akash has confirmed that order.

## Grok Bot

Use the Node entry. It speaks MCP stdio with `@modelcontextprotocol/sdk` (the same transport bambu-mcp uses) and keeps one Python worker for the tool calls.

```bash
npm install
npm run build
```

```bash
node /absolute/path/to/kalshi-mcp/dist/index.js
```

Set these in the host env, not on the command line:

- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY_PATH`
- `KALSHI_SAFE_MODE=1` for a read-only host
- `KALSHI_SAFE_MODE=0` for the Finance Engineer sleeve, after install

`KALSHI_SAFE_MODE=1` is the house default. Order tools stay unregistered. `find_best_bets` only reads. Do not commit the key file.

The Finance Engineer sleeve sets `KALSHI_SAFE_MODE=0` and restarts the host. `place_order`, `cancel_order`, `amend_order`, and `decrease_order` then show up. Every one of those calls still needs `confirm: true`. Opening risk still has to fit the caps: about $2 until Kalshi fill history shows the sleeve is profitable, hard max $15, at most 15% of the sleeve in one market, and at most 30% in one `corr_group`. There is no withdraw tool and no deposit tool. The daily and end-of-day prompt is `fe_routine` and `harness/fe-grok-bot-routine.md`.

```json
{
  "mcpServers": {
    "kalshi-readonly": {
      "command": "node",
      "args": ["/absolute/path/to/kalshi-mcp/dist/index.js"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/private.pem",
        "KALSHI_SAFE_MODE": "0"
      }
    }
  }
}
```

Use `"KALSHI_SAFE_MODE": "1"` when this host should stay read-only. `"0"` is only for the Finance Engineer sleeve. Restart after you change it so the tool list reloads.

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

`mcp.json` in this repo is the plugin-host launch (`python3` and `${PLUGIN_ROOT}/server.py`). Grok Bot should use the Node command above instead.

## Tools

| Tool | What it does |
| --- | --- |
| `exchange_status` | Public exchange status |
| `list_markets` | Public markets. Optional `limit` (default 5), `status`, `ticker` |
| `find_best_bets` | Read-only rank. See Bets. Does not place orders. Rows include `edge_net_cents`, `flb_band`, `kelly_frac`, and `stake_mode` |
| `fe_routine` | Daily and end-of-day prompt for the Finance Engineer. Does not trade |
| `cash_or_positions` | Cash and positions. `include`: `balance`, `cash`, `positions`, `both` (default), or `fills`. Optional `limit` (default 50) |
| `list_open_orders` | Resting orders only (`status=resting`). Optional `ticker`, `limit` (default 100), `cursor`, `subaccount`. Read. No confirm |
| `place_order` | One order. Hidden until safe mode is off. Requires `confirm: true` |
| `cancel_order` | One cancel. Hidden until safe mode is off. Requires `confirm: true` |
| `amend_order` | Price and total fillable count. Hidden until safe mode is off. Requires `confirm: true` |
| `decrease_order` | Reduce resting size. Hidden until safe mode is off. Requires `confirm: true` |

`cash_or_positions` returns `balance_cents`, `balance_dollars`, and `cash` (the same official dollar string). `portfolio_value` is included only when the balance endpoint sends it. `market_positions` and `event_positions` each include `ticker` and `qty` when the API sends a quantity. Market `side` is the official sign of that quantity: positive YES, negative NO. `avg` and `mark` are copied only when the API sends them. `include=fills` reads recent fills (ticker, side, qty, official prices). This server does not compute remaining-to-recover.

## Bets

`find_best_bets` is a read. The dry-run command is `python3 -m kalshi_readonly.recommend --fixture tests/fixtures/recommend_scan.json`. That command uses the file only. It does not call Kalshi and it does not place orders.

The score is `estimated_confidence * payout_ratio / stake_needed`. `stake_needed` is the ask for one contract. `payout_ratio` is the profit per dollar you stake if that side wins. A 15 cent contract with high confidence sorts ahead of an 82 cent contract, because the 82 cent contract ties up more cash for a smaller payout.

Pass `beliefs` when you can name the evidence: `ticker`, `side` (`yes` or `no`), `confidence` (0 to 1), and an optional `evidence` string. At most 20. Those tickers are one `GET /markets?tickers=...` request. The server does not fetch each ticker on its own, and it does not pull an order book per market. Without beliefs, you get a `research_queue` and no recommendations. A queue row is not a bet.

Defaults, all overridable inside a fixed range:

| Knob | Default | Range |
| --- | --- | --- |
| `min_confidence` | 0.55 | 0.50 to 0.99 |
| `min_edge` | 0.08 | 0 to 0.90 |
| `max_price` | 0.50 | 0.05 to 0.84 |
| `max_risk_dollars` | 5 | 1 to 25 |
| `min_volume` | 20 contracts of 24h volume | 0 to 1000000 |
| `min_ask_size` | 1 | 0 to 100000 |
| `min_hours_to_expiry` | 2 | 0 to 168 |
| `max_hours_to_expiry` | 1440 (60 days) | above the minimum, up to 8760 |
| `max_pages` | 2 | 1 to 4 |
| `page_size` | 100 | 1 to 200 |
| `limit` | 5 rows | 1 to 10 |

Prices at or above 0.85 are never recommended. The scan also skips multivariate combos (`mve_filter=exclude`) so a page is not spent on them. A successful list is cached for about 45 seconds in the process. The Node entry keeps that process alive across tool calls.

Every response includes `places_orders: false` and the sentence `do not place until Akash names the trade`. Each ranked row also reports `edge_net_cents` after the fee dome (`ceil(0.07 * contracts * price * (1 - price))` cents), `flb_band`, `kelly_frac` of 0.25, and `stake_mode`. A price at or under 10 cents is skipped unless that net edge is at least 8 cents. The rank score itself is unchanged. The Finance Engineer routine is `fe_routine` and `harness/fe-grok-bot-routine.md`.

## Trade

Opening risk is capped in process. The server does not set `confirm` for you.

| Cap | Default |
| --- | --- |
| Stake | $2 until fill history shows the sleeve is profitable. Prices under 25 cents stay at $2 |
| Hard max | $15 per trade. A setting above 15 is ignored |
| One market | 15% of the $71 sleeve |
| One corr_group | 30% of the sleeve. Pass `corr_group` on `place_order` |
| New notional per UTC day | $10 |

Cancels and decreases do not add risk and are not size-capped. An amend that would increase risk is capped. Size above the $2 default requires at least three closing trades and a positive net on this account.

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
