# kalshi-readonly

Kalshi Trade API v2 MCP over stdio (`kalshi-mcp-v1-readonly-fleet`). Read tools cover exchange status, markets, cash, positions, and a ranked list of small-stake ideas. Trade tools place, cancel, amend, or decrease one order. They are gated behind `KALSHI_SAFE_MODE`. They are not absent from v1.

Fleet default is `KALSHI_SAFE_MODE=1`. `tools/list` then omits `place_order`, `cancel_order`, `amend_order`, and `decrease_order`, and those handlers still refuse the call. The Finance Engineer harness (`harness/SKILL.md`) is the only policy that sets `KALSHI_SAFE_MODE=0`, and only on the Finance Engineer host. There is no separate Kalshi role harness. There is no deposit tool, no withdraw tool, and no auto-trading tool. `find_best_bets` never places an order.

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
| `KALSHI_API_KEY_ID` | portfolio and trade calls | API key id. Or set `KALSHI_API_KEY_ID_PATH` |
| `KALSHI_API_KEY_ID_PATH` | alternative to the key id | File containing the key id. If both id env vars are unset, `~/.secrets/kalshi/key_id` is used when that file exists |
| `KALSHI_PRIVATE_KEY_PATH` | one of these | PEM file path. If PEM env vars are unset, `~/.secrets/kalshi/private.pem` is used when that file exists |
| `KALSHI_PRIVATE_KEY_PEM` | one of these | PEM contents |
| `KALSHI_API_BASE` | no | Default `https://api.elections.kalshi.com/trade-api/v2` |
| `KALSHI_SAFE_MODE` | no | Defaults to on. See Trade. |

Missing auth fails before any portfolio or order request. The private key is used only to sign. It is not logged and not returned. If a response or error would contain key material, that text is dropped.

Signing matches the cash CLI for GET, POST, and DELETE: `timestamp_ms + METHOD + path` (path includes `/trade-api/v2`, query string excluded, body excluded), RSA-PSS with SHA-256 and a digest-length salt, base64 signature. Headers are `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP`, and `KALSHI-ACCESS-SIGNATURE`.

## Prove

No-network fleet check. The printed names must include `exchange_status`, `cash_or_positions`, `find_best_bets`, and `fe_routine`, and must omit `place_order`, `cancel_order`, `amend_order`, and `decrease_order`.

```bash
KALSHI_SAFE_MODE=1 python3 -c 'from kalshi_readonly.tools import registered_tools; print([t["name"] for t in registered_tools()])'
printf '%s\n' '{"id":1,"op":"list"}' | KALSHI_SAFE_MODE=1 python3 -m kalshi_readonly.dispatch
```

On the box, after the host is up: call `exchange_status`, then `cash_or_positions` with `include=both`. The same cash read from the CLI, which does not place:

```bash
export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < /path/to/key_id)"
export KALSHI_PRIVATE_KEY_PATH=/path/to/private.pem
python3 -c 'import json; from kalshi_readonly.tools import cash_or_positions; print(json.dumps(cash_or_positions({"include":"both","limit":5}), indent=2))'
```

`~/.secrets/kalshi/key_id` and `private.pem` are enough when those env vars are unset. A host that says Not connected needs `npm run build` and a restart so `node dist/index.js` is the live process. Then `tools/list` is the check.

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

- `KALSHI_API_KEY_ID` or `KALSHI_API_KEY_ID_PATH` (default `~/.secrets/kalshi/key_id`)
- `KALSHI_PRIVATE_KEY_PATH` or `KALSHI_PRIVATE_KEY_PEM` (default `~/.secrets/kalshi/private.pem`)
- `KALSHI_SAFE_MODE=1` on every fleet host

`KALSHI_SAFE_MODE=1` is the fleet default. Missing or empty stays on. `tools/list` omits `place_order`, `cancel_order`, `amend_order`, and `decrease_order`. `find_best_bets` only reads. Do not commit the key file.

```json
{
  "mcpServers": {
    "kalshi-readonly": {
      "command": "node",
      "args": ["/absolute/path/to/kalshi-mcp/dist/index.js"],
      "env": {
        "KALSHI_API_KEY_ID_PATH": "/absolute/path/to/.secrets/kalshi/key_id",
        "KALSHI_PRIVATE_KEY_PATH": "/absolute/path/to/.secrets/kalshi/private.pem",
        "KALSHI_SAFE_MODE": "1"
      }
    }
  }
}
```

Omit the path env vars when `~/.secrets/kalshi/key_id` and `private.pem` already exist for that user. Do not commit those files.

Finance Engineer override, and only that host. `harness/SKILL.md` owns the decision. Do not add a second Kalshi role harness. Install, in order:

1. `npm run build` so `dist/index.js` matches this tree.
2. Point the host at `node /absolute/path/to/kalshi-mcp/dist/index.js` with `KALSHI_SAFE_MODE=0`.
3. Restart the host. The Node process answers MCP `initialize` before the Python worker finishes `tools/list`, then `tools/list` waits for that list. The list is fixed for the life of the process (`listChanged` is false). A restart that leaves no live `dist/index.js` shows up as Not connected. Rebuild and restart so the host spawns the process again.
4. Prove the new process with `tools/list`. It must include `fe_routine`, `find_best_bets`, `place_order`, `cancel_order`, `amend_order`, and `decrease_order`. stderr from the entry includes `safeMode=off` and those names. `exchange_status` is the cheap public call that the worker can reach Kalshi. It does not place.

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

`"KALSHI_SAFE_MODE": "0"` in the block above is the Finance Engineer host only. Every other host stays at `"1"`. Restart after you change it so the tool list reloads. `listChanged` stays false for the life of the process.

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
| `find_best_bets` | Read-only rank. See Bets. Does not place orders. Rows include `edge_net_cents`, `flb_band`, `kelly_frac`, `stake_mode`, `side_exec`, `days_to_res`, `spread_cents`, `depth_at_ask`, `fee_cents_est`, `corr_group_hint`, and `hold_to_res_default`. `rate_limited` is true when a later scan page hit the retry budget |
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
| `max_price` | 0.84 | 0.05 to 0.84 |
| `max_risk_dollars` | 5 | 1 to 25 |
| `min_volume` | 20 contracts of 24h volume (weaker proxy) | 0 to 1000000 |
| `min_lifetime_volume` | 1000 contracts lifetime | 0 to 100000000 |
| `min_ask_size` | 1 | 0 to 100000 |
| `min_hours_to_expiry` | 2 | 0 to 168 |
| `max_hours_to_expiry` | 1440 (60 days) | above the minimum, up to 8760 |
| `max_pages` | 1 | 1 to 4 |
| `page_size` | 100 | 1 to 200 |
| `limit` | 5 rows | 1 to 10 |

Prices at or above 0.85 are never recommended. The scan also skips multivariate combos (`mve_filter=exclude`) so a page is not spent on them. Public `GET /markets` counts against the same IP budget as signed calls, so the default scan is one page. A successful list is cached for about 5 minutes in the process. Concurrent scans of the same page share one in-flight request. The Node entry keeps that process alive across tool calls.

`rate_limited` is false on a normal read. If a later scan page still gets HTTP 429 or 503 after the retry budget, the tool returns the pages already fetched, sets `rate_limited` to true, and does not place. That partial list is reused for about 60 seconds. A rate limit on the first page fails the tool with an error that starts with `rate_limited`. Do not scan again immediately.

HTTP 429 and 503 are tried at most 3 times. A `Retry-After` value is honored up to 3 seconds. Without that header, or when it is not a delay or a date, the wait is a short exponential backoff with jitter (0.25s, then 0.5s, capped at 3 seconds). The error is `rate_limited` only after that budget is spent.

`fee_cents_est` is the official schedule for the suggested role: taker `ceil(M * 0.07 * C * P * (1-P) * 100)` cents, maker `ceil(M * 0.0175 * C * P * (1-P) * 100)` when the market carries `fee_multiplier` (also `series_fee_multiplier` or `fee_multiplier_fp`). Unknown M means taker 1 and maker 0. `edge_net_cents` subtracts that taker dome, half the spread, and a depth haircut (0 when the ask shows at least 3 contracts, otherwise the shortfall capped at 2 cents). `maker_flag` is true when `side_exec` is `maker`.

Every response includes `places_orders: false` and the sentence `Finance Engineer may place under the caps with confirm true`. The tool still does not place. Each ranked row reports `edge_net_cents` after the fee dome (`ceil(0.07 * contracts * price * (1 - price))` cents), `flb_band`, `kelly_frac` of 0.25, `stake_mode`, `side_exec` / `SIDE_EXEC`, `days_to_res` / `DAYS_TO_RES`, `spread_cents`, `depth_at_ask`, `fee_cents_est`, `corr_group_hint`, and `hold_to_res_default` / `HOLD_TO_RES_DEFAULT`. `min_edge` (default 0.08) is probability points and is separate from `edge_net_cents`. A taker quote in the `<10¢` band is dropped unless the belief sets `allow_longshot` true, that probability edge is at least 0.08, and the suggested stake is forced to $2. Lifetime volume of at least 1000 passes; 24h `min_volume` is the weaker proxy, and both numbers are on the row. A maker quote whose taker fee is within 2 cents sorts 0.0001 ahead of an equal score. The displayed score stays the formula. `max_price` defaults to 0.84, the hard ceiling, so a 50¢–84¢ contract with edge is eligible. The previous default of 0.50 hid that band; pass `max_price` 0.50 to restore it. The Finance Engineer routine is `fe_routine` and `harness/fe-grok-bot-routine.md`.

## Trade

Opening risk is capped in process. The server does not set `confirm` for you.

| Cap | Default |
| --- | --- |
| Stake | $2 until fill history shows the sleeve is profitable. Prices under 25 cents stay at $2 |
| Hard max | $15 per trade. A setting above 15 is ignored |
| One market | 15% of the $71 sleeve |
| One corr_group | 30% of the sleeve. `corr_group` is required on opening risk (`place_order`, and `amend_order` because an amend is checked as new risk) |
| New notional per UTC day | $10 |

Cancels and decreases do not add risk and are not size-capped. An amend that would increase risk is capped. Size above the $2 default requires at least three closing trades and a positive net on this account.

`KALSHI_SAFE_MODE` defaults to on. Missing, empty, `1`, `true`, `yes`, `on`, and any other value keep it on. Only `0`, `false`, `no`, and `off` turn it off. While it is on:

- `place_order`, `cancel_order`, `amend_order`, and `decrease_order` are omitted from `tools/list`.
- Calling one of those handlers anyway returns an error and does not hit the network.
- `list_open_orders` stays registered. It is a signed GET of resting orders.

The Finance Engineer harness is the only unlock. After that host sets `KALSHI_SAFE_MODE=0`, restart it so `tools/list` reloads. Every mutating call still requires `confirm` to be the boolean `true`. `false`, `"true"`, `1`, and a missing confirm are refused before auth and before HTTP. The confirm flag is not copied into the Kalshi body. Fleet hosts do not set `0`.

| Tool | Kalshi route | Required arguments |
| --- | --- | --- |
| `place_order` | `POST /portfolio/events/orders` | `ticker`, `side` (`bid` or `ask`), `count`, `price`, `time_in_force`, `self_trade_prevention_type`, `corr_group`, `confirm` |
| `cancel_order` | `DELETE /portfolio/events/orders/{order_id}` | `order_id`, `confirm`. Pass `market_ticker` so Kalshi can auto-route |
| `amend_order` | `POST /portfolio/events/orders/{order_id}/amend` | `order_id`, `ticker`, `side`, `price`, `count`, `corr_group`, `confirm` |
| `decrease_order` | `POST /portfolio/events/orders/{order_id}/decrease` | `order_id`, `confirm`, and exactly one of `reduce_by` or `reduce_to` |
| `list_open_orders` | `GET /portfolio/orders?status=resting` | none |

`bid` buys YES. `ask` sells YES. Prices are YES-book dollar strings strictly between 0 and 1, such as `0.4200`. `count` is a contract string such as `1.00` (a positive integer is accepted). Amend `count` is already filled plus the desired resting remainder, not a reduce-by amount. Decreasing size keeps queue position. A price change or a larger size does not. Use `decrease_order` to reduce.

`time_in_force` is `fill_or_kill`, `good_till_canceled`, or `immediate_or_cancel`. The maker path is `time_in_force=good_till_canceled` and `post_only=true`. `post_only` with `fill_or_kill` or `immediate_or_cancel` is refused here. Kalshi also rejects a `post_only` order that would cross, so a maker flag cannot silently take. `self_trade_prevention_type` is `taker_at_cross` or `maker`. `expiration_time` is only valid with `good_till_canceled`. `reduce_only` is only valid with `immediate_or_cancel` and does not require `corr_group`. Pass `client_order_id` yourself when you want dedup. This server does not invent one.

A successful `place_order` echoes `role` (`maker` when `post_only` is true, otherwise `taker`), `fee_cents_est` (0 for the default maker multiplier, otherwise the taker dome), and `day_spend_remaining` from the live book against the soft $10 UTC-day cap. `fe_routine` does not invent that remaining figure. Size above $2 still needs a profitable fill history. There is no withdraw and no deposit.

Optional place fields: `client_order_id`, `expiration_time`, `post_only`, `reduce_only`, `cancel_order_on_pause`, `subaccount`, `order_group_id`, `exchange_index`.

Demo host, if you use one: `KALSHI_API_BASE=https://demo-api.kalshi.co/trade-api/v2`.

There is no batch place, no batch cancel, no withdraw, and no deposit.
