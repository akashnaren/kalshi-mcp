---
name: cash-or-positions
description: >-
  Use when reading official Kalshi cash or positions via kalshi-readonly MCP
  instead of the browser. Requires API key env. Do not set confirm or turn off safe mode.
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
Balance returns `balance_cents` and `balance_dollars` (`cash` is that dollar string). `portfolio_value` only when the API sends it. Positions list market and event rows with ticker and `qty`. Market `side` is the official quantity sign (positive YES, negative NO) unless the payload already has `side`. `avg` and `mark` only when the API sends them. Do not invent remaining-to-recover.

`list_open_orders` is a read of resting orders and works while safe mode is on. `place_order`, `cancel_order`, `amend_order`, and `decrease_order` stay hidden until the operator sets `KALSHI_SAFE_MODE=0`, and each call still needs `confirm: true` from that person. Do not set confirm yourself. Do not flip safe mode. There is no deposit, withdraw, or strategy tool.
