---
name: trade-orders
description: >-
  Use when Akash explicitly asks to place, cancel, amend, or reduce one Kalshi
  order. Safe mode is on by default. Never set confirm yourself. Never
  withdraw, deposit, or run an autonomous strategy.
---
# Trade orders

## When
Akash has explicitly asked for one order action and named the market, side, size, and price, or the order id to cancel or reduce.

## Sequence
1. If `KALSHI_SAFE_MODE` is unset or not `0`, stop. Mutating tools are unregistered. Ask Akash before anyone sets `KALSHI_SAFE_MODE=0` and restarts the server.
2. Read `list_open_orders` or `cash_or_positions` when the request needs resting orders or cash. Those reads do not need confirm.
3. Call `place_order`, `cancel_order`, `amend_order`, or `decrease_order` only with the arguments Akash supplied. Include `confirm: true` only when he provided that confirmation. If he did not, stop and ask. Do not fill in confirm.
4. There is no dollar max in this server. Do not invent a size. Do not loop orders, chase a max profit, or place a batch.

## Validate
`side` is `bid` (buy YES) or `ask` (sell YES). `price` is a YES-book dollar string between 0 and 1. `count` is a contract count. Amend `count` means filled plus desired remaining. Decrease takes exactly one of `reduce_by` or `reduce_to`. Cancel should include `market_ticker` from the open order. No withdraw or deposit tool exists.

`find_best_bets` is a read. A row from it is not an order. Do not place that row unless Akash names the trade and confirms.
