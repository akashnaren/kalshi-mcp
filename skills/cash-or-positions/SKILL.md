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
3. Recent fills are a separate read: `cash_or_positions` with `include=fills`. `both` does not include fills.
4. If auth error, stop and ask Akash for a read-scoped API key — do not open the website unless he says so.

## Validate
Balance returns `balance_cents` and `balance_dollars` (`cash` is that dollar string). `portfolio_value` only when the API sends it. Positions list market and event rows with ticker and `qty`. Market `side` is the official quantity sign (positive YES, negative NO) unless the payload already has `side`. `avg` and `mark` only when the API sends them. Do not invent remaining-to-recover.

## Trade
This skill only reads. Order tools are separate and stay off while `KALSHI_SAFE_MODE` is on (the default). When it is off, they still require boolean `confirm: true` from Akash. There is no dollar cap. Do not place, cancel, or withdraw from this skill.
