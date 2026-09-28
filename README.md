# kalshi-mcp

Kalshi connector for the Finance Engineer sleeve. It scans markets, sizes ideas, and can place or exit orders inside hard caps. It cannot withdraw or deposit.

The sleeve starts around $71.

## The rule

Gate on `edge_net` (`edge_net_cents`) after the Kalshi fee dome, the spread, and `depth_at_limit`. Win rate and payoff size are not reasons to trade. Polymarket is not Kalshi. Re-estimate on this sleeve's Kalshi fills.

The Kalshi-native prior is Bürgi, Deng, and Whelan, [Makers and Takers](https://www.karlwhelan.com/Papers/Kalshi.pdf).

`fee_dome` is `ceil_cent(0.07 * contracts * price * (1 - price))`. Near 50 cents that is about 2 cents a contract. MIN_EDGE is the max of 2 cents and twice that fee.

`flb_band` tags the price. A price at or under 10 cents is a taker skip unless `edge_net` is at least 8 cents, and that stake stays a fixed $2. Buying favorites at or above 97 cents because the win rate looks high is a skip. Prefer a maker when `edge_net` is small. The default is hold to settlement unless a falsifier hits or `edge_net` flips to −2 cents. A 1–3 cent mark move is not an exit.

Every idea lists `edge_net`, `flb_band`, `side_exec`, `stake_mode`, `kelly_frac`, `horizon_days`, `category_tag`, `corr_group`, `model_sources`, and `settlement_match_score`. A hunch is rejected. Fee-blind backtests and cross-venue arb without settlement identity are skipped. Category multipliers are not used. Do not auto-arb.

Size is quarter Kelly (λ=0.25). Half Kelly needs an explicit out-of-sample calibration log. Profitable fill history does not raise λ by itself. Under 25 cents the stake stays $2. At or above 50 cents, modest size can reach $8 once the sleeve has been profitable, still inside the hard max.

## Caps

These are checked in the process before any order that adds risk. Defaults:

| Cap | Default | Env |
| --- | --- | --- |
| Stake | About a $2 default. Under 25 cents it stays $2. At or above 50 cents, $2–$8 after the sleeve is profitable | `KALSHI_DEFAULT_DOLLARS_PER_TRADE` |
| Hard max per trade | $15 ceiling. Values above 15 are ignored | `KALSHI_MAX_DOLLARS_PER_TRADE` |
| Per market | 15% of the sleeve | `KALSHI_MAX_SLEEVE_FRACTION` |
| Per corr_group | 30% of the sleeve | `KALSHI_MAX_GROUP_FRACTION` |
| Per event | $15 and 20% of the sleeve | (in process) |
| Deployed | 40% of the sleeve | (in process) |
| Open positions | aim 8–15, hard stop at 15 | (in process) |
| Sleeve | $71 | `KALSHI_SLEEVE_DOLLARS` |
| New notional per UTC day | $10 | `KALSHI_MAX_DAILY_NOTIONAL` |

15% of $71 is $10.65, so one market cannot take the sleeve. Diversify by independent risk drivers, not by ticker count. The daily cap stops at $10, which binds before the $15 ceiling unless those caps are set wider. The $15 ceiling on a high-probability idea is available only after the sleeve's own fills are profitable, and still only inside quarter Kelly.

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

That checks edge_net after the fee dome, the 10 cent and 97 cent skips, quarter Kelly, the $2 default, the hard max $15, the win-history gate, the 15% / corr_group / $10 caps, the safe-mode lock, and that the stdio process starts.
