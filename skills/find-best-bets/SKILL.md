---
name: find-best-bets
description: >-
  Rank open Kalshi contracts for a small stake and a high payout when
  confidence is high. Read only. The Finance Engineer may place a surviving
  row under the caps with confirm true.
---

# Find best bets

## When
Someone wants a ranked Kalshi idea, not an order. The longer checklist is `harness/SKILL.md`.

## Sequence
1. Keep `KALSHI_SAFE_MODE` on. This tool does not trade.
2. Call `find_best_bets`. Omit beliefs to get a research queue. Pass beliefs only after you can name evidence and your confidence in `yes` or `no` is high.
3. Read `recommendations` for rationale, assumed edge, and suggested max dollars risked. A research-queue row is not a bet. If `rate_limited` is true, keep the partial `research_queue` and do not scan again immediately.
4. The tool does not place. On the Finance Engineer host (`KALSHI_SAFE_MODE=0`), a surviving row may be placed with `place_order`, `confirm: true`, and `corr_group`, inside the caps. Fleet hosts stay at `KALSHI_SAFE_MODE=1`. The default scan is one page.

## Validate
`side` is `yes` or `no`. Score prefers a cheap ask. `max_price` defaults to 0.84. Prices at or above 0.85 are dropped. Thin markets and bad expiry are dropped. A row with `edge_net_cents` at or below 0 is dropped. A taker quote under 10 cents needs `allow_longshot`, `edge_net_cents` at least 8, and a $2 stake. A 10–25 cent quote is capped at $2. Suggested risk defaults to 5 dollars above that band. Do not set `confirm` from this skill.
