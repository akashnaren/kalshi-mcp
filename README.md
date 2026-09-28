# kalshi-mcp

Kalshi connector for the Finance Engineer sleeve. It scans markets, sizes ideas, and can place or exit orders inside hard caps. It cannot withdraw or deposit.

The sleeve starts around $71.

## The rule

Prefer a small stake and a high payout when named indicators are strong. That means confidence of at least 0.65 and an ask of at most $0.40. The score is `confidence * payout / stake`.

Or take a modest size on a highly likely side, confidence at least 0.85, when the signals are clear. Modest means half of the per-idea cap.

Every idea lists its indicator keys. A hunch is rejected.

A 15 cent contract at 0.80 confidence scores about 4.53 and can be sized up to $2. A 70 cent contract at 0.90 confidence is the likely lane and is sized smaller. A 5 cent contract at 0.40 confidence is dropped.

## Caps

These are checked in the process before any order that adds risk. Defaults:

| Cap | Default | Env |
| --- | --- | --- |
| Per idea | $2 | `KALSHI_MAX_DOLLARS_PER_IDEA` |
| Per market | 15% of the sleeve | `KALSHI_MAX_SLEEVE_FRACTION` |
| Sleeve | $71 | `KALSHI_SLEEVE_DOLLARS` |
| New notional per UTC day | $10 | `KALSHI_MAX_DAILY_NOTIONAL` |

15% of $71 is $10.65, so one market cannot take the sleeve. A single idea still stops at $2. The daily cap stops at $10 even if you have room in a market.

Take profit defaults to +50% (`KALSHI_TAKE_PROFIT_RETURN`). A cut defaults to -40% (`KALSHI_CUT_LOSS_RETURN`). Anything in between is a hold.

## Safe mode

`KALSHI_SAFE_MODE` defaults on, including when it is unset. A fresh checkout will not trade.

After install, the Finance Engineer sets `KALSHI_SAFE_MODE=0`. Orders still need `confirm:true` on that call, and they still have to fit the caps. Turning safe mode off is not a blank check.

## Run it

Node 18 or newer.

```bash
npm install
npm test
npm start
```

`npm start` speaks MCP over stdin and stdout. That is the process Grok Bot should launch.

```json
{
  "mcpServers": {
    "kalshi": {
      "command": "node",
      "args": ["/absolute/path/to/kalshi-mcp/src/index.js"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/home/you/.kalshi/kalshi.key",
        "KALSHI_SAFE_MODE": "0"
      }
    }
  }
}
```

Put the real key id in the environment of the process, not in git. The private key stays outside this repo. Leave the cap env vars unset to keep the defaults.

| Variable | Purpose |
| --- | --- |
| `KALSHI_API_KEY_ID` | Key id from Kalshi. Public scans work without it. Trading needs it. |
| `KALSHI_PRIVATE_KEY_PATH` | Path to the PEM. RSA or Ed25519. Never commit this file. |
| `KALSHI_SAFE_MODE` | Unset or `1` refuses every order. `0` allows orders that pass `confirm:true` and the caps. Set `0` only for this sleeve, after install. |
| `KALSHI_BASE_URL` | Optional. Defaults to `https://external-api.kalshi.com/trade-api/v2`. |

## Tools

| Tool | What it does |
| --- | --- |
| `get_balance`, `get_positions`, `get_fills`, `get_orders` | Read the account. |
| `find_best_bets` | Rank ideas and suggest a size inside the caps. Does not send an order. |
| `review_positions` | Mark each position take profit, cut, or hold. |
| `place_order` | Needs `confirm:true`, safe mode off, and room under the caps. |
| `exit_position` | Close some or all of one position. Needs `confirm:true`. |
| `cancel_order`, `decrease_order` | Shrink resting risk. Need `confirm:true`. |
| `amend_order` | Reprice or resize. A bigger size is cap-checked. |

`bid` buys YES. `ask` sells YES. Buying NO is an ask priced at one minus the NO price.

## Daily and end of day

The operating notes are in `harness/SKILL.md`. The text to paste into a Grok Bot routine is in `harness/fe-grok-bot-routine.md`.

DAILY: read the account, scan with named signals, place the suggested size, stop when a cap says no.

EOD: review hold, cut, or take profit, then exit or shrink resting orders. No new risk past the daily cap.

## Tests

```bash
npm test
```

That checks the score, both lanes, the $2 / 15% / $10 caps, the safe-mode lock, and that the stdio process starts.
