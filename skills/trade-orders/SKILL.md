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
1. If `KALSHI_SAFE_MODE` is unset or not `0`, stop. Mutating tools are unregistered. Fleet hosts stay at `1`. Only the Finance Engineer host unlocks them, and `harness/SKILL.md` owns that decision. This skill does not set `KALSHI_SAFE_MODE`.
2. Read `list_open_orders` or `cash_or_positions` when the request needs resting orders or cash. Those reads do not need confirm.
3. Call `place_order`, `cancel_order`, `amend_order`, or `decrease_order` only with the arguments Akash supplied. Include `confirm: true` only when he provided that confirmation. If he did not, stop and ask. Do not fill in confirm.
4. Opening size is capped: $2 until the sleeve fill history is profitable, hard max $15, at most 15% of the sleeve in one market, at most 30% in one corr_group. `corr_group` is required on opening risk. Missing, empty, and `none` are refused. Examples: `nfl_week_N`, `city_weather_YYYYMMDD`, `fed_meeting_YYYYMM`. Prefer `post_only: true` with `time_in_force: good_till_canceled`. Do not loop orders, chase a max profit, or place a batch. No withdraw and no deposit.

## Validate
`side` is `bid` (buy YES) or `ask` (sell YES). `price` is a YES-book dollar string between 0 and 1. `count` is a contract count. Amend `count` means filled plus desired remaining. Decrease takes exactly one of `reduce_by` or `reduce_to`. Cancel should include `market_ticker` from the open order. No withdraw or deposit tool exists.

`find_best_bets` is a read. A row from it is not an order. The Finance Engineer host may place that row under the caps with `confirm: true` and `corr_group`. This skill still does not set `confirm`.
