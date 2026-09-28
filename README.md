# kalshi-mcp

A small Kalshi connector for Grok Bot and Cursor. It reads balance, positions, and fills, and it ranks contracts you already have evidence for.

It recommends. It does not trade unless you turn safe mode off and pass `confirm:true` on that specific call. The scanner never does either of those.

## The rule

Prefer a low stake and a high payout when confidence that this side wins is high.

High means at least 0.65. Confidence has to come from named indicators. Every recommendation lists those keys. A hunch does not count.

The score is `confidence * payout / stake`.

- Stake is the ask you would pay, in dollars.
- Payout is the profit if that side wins. On a one-dollar contract that is `1 - ask`.

A 15 cent contract at 0.80 confidence scores about 4.53. A 70 cent contract at 0.90 confidence scores about 0.39. The cheap one ranks first. A 5 cent contract at 0.40 confidence is dropped, because 0.40 is not high.

Liquidity still has to be there. Defaults: 200 contracts of volume in the last day, 100 of open interest, bid-ask spread of at most 8 cents, and at least 10 contracts at the ask. The scan reads those quotes from the market list. It does not request an order book for every market.

## Run it

Node 18 or newer.

```bash
npm install
npm test
npm start
```

`npm start` speaks MCP over stdin and stdout. That is the process Grok Bot should launch. Logs go to stderr, not stdout.

```json
{
  "mcpServers": {
    "kalshi": {
      "command": "node",
      "args": ["/absolute/path/to/kalshi-mcp/src/index.js"],
      "env": {
        "KALSHI_API_KEY_ID": "your-key-id",
        "KALSHI_PRIVATE_KEY_PATH": "/home/you/.kalshi/kalshi.key",
        "KALSHI_SAFE_MODE": "1"
      }
    }
  }
}
```

Put the real key id in the environment of the process, not in git. The private key is a PEM file that stays outside this repo.

| Variable | Purpose |
| --- | --- |
| `KALSHI_API_KEY_ID` | Key id from Kalshi → Account & security → API Keys. Public market scans work without it. Balance, positions, and fills need it. |
| `KALSHI_PRIVATE_KEY_PATH` | Path to the PEM. RSA or Ed25519. Never commit this file. |
| `KALSHI_SAFE_MODE` | `1` is the default, including when the variable is unset. Mutations are refused. |
| `KALSHI_BASE_URL` | Optional. Defaults to `https://external-api.kalshi.com/trade-api/v2`. Demo: `https://external-api.demo.kalshi.co/trade-api/v2`. |

Copy `.env.example` if you want a local env file. `.env` is gitignored.

## Tools

| Tool | What it does |
| --- | --- |
| `get_balance` | Portfolio balance. |
| `get_positions` | Open positions. |
| `get_fills` | Recent fills. |
| `find_best_bets` | Read-only ranker. You pass signals. It returns recommendations with keys, stake, payout, and score. |
| `place_order` | Refused while safe mode is on. Also refused unless `confirm:true`. `bid` buys YES. `ask` sells YES. |
| `cancel_order` | Same two locks. Needs the order id and the market ticker. |

There is also an `fe_routine` prompt with the same instructions as the Grok Bot snippet.

A signal looks like this:

```json
{
  "key": "rcp_polling_average",
  "side": "yes",
  "confidence": 0.72,
  "detail": "RCP average is 61 percent versus a 22 cent ask",
  "market_ticker": "KXEXAMPLE-26-T1"
}
```

`market_ticker` is the narrowest scope. Use `event_ticker` or `series_ticker` when the indicator covers a group. The scanner refuses a pile of unrelated queries (more than 8) so it stays fast.

If two signals disagree on the side, that market is skipped as a conflict. Two signals that agree keep both keys. Confidence is the stronger of the two, not a blend, so two weak hunches cannot add up to a recommendation.

## For the Finance Engineer bot

The operating notes are in `harness/SKILL.md`. The text to paste into a Grok Bot routine is in `harness/fe-grok-bot-routine.md`.

The routine's job is to gather named signals, call `find_best_bets`, and show the rows. It does not call `place_order`.

## Tests

```bash
npm test
```

That checks the score, the confidence floor, the liquidity filters, the safe-mode locks, request signing, and that the stdio process actually starts.
