---
name: find-best-bets
description: >-
  Rank open Kalshi contracts for a small stake and a high payout when
  confidence is high. Read only. Do not place until Akash names the trade.
---

# Find best bets

## When
Someone wants a ranked Kalshi idea, not an order. The longer checklist is `harness/SKILL.md`.

## Sequence
1. Keep `KALSHI_SAFE_MODE` on. This tool does not trade.
2. Call `find_best_bets`. Omit beliefs to get a research queue. Pass beliefs only after you can name evidence and your confidence in `yes` or `no` is high.
3. Read `recommendations` for rationale, assumed edge, and suggested max dollars risked. A research-queue row is not a bet.
4. Stop after the list. The last line is: do not place until Akash names the trade.

## Validate
`side` is `yes` or `no`. Score prefers a cheap ask. Default filters drop prices above 0.50, thin markets, and bad expiry. Suggested risk defaults to 5 dollars. Do not set `confirm` from this skill.
