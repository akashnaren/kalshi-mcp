---
name: cash-or-positions
description: >-
  Use when reading official Kalshi cash or positions via kalshi-readonly MCP
  instead of the browser. Requires API key env; never trade or withdraw.
---
# Cash or positions

## When
CFO/Spend needs official Kalshi cash or open positions.

## Sequence
1. Prefer `exchange_status` / `list_markets` if only public data needed.
2. For balances: `cash_or_positions` with `include=balance` or `both`.
3. Optional recent fills: `cash_or_positions` with `include=fills`.
4. If auth error, stop and ask Akash for a read-scoped API key — do not open the website unless he says so.

## Validate
Balance returns cents / balance_dollars (`cash` is that same official dollar string). `portfolio_value` only when the API sends it. Positions list tickers and official quantity (`qty`). `side`, `avg`, and `mark` only when the payload has them. Do not invent remaining-to-recover. No order endpoints exist in this server.
