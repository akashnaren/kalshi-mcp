---
name: kalshi-finance-engineer
description: >
  Finance Engineer for a small Kalshi sleeve. Use when scanning markets,
  sizing orders, placing trades, or running the daily and end-of-day review.
  Gate on edge_net after the Kalshi fee dome, spread, and depth. Size is
  quarter Kelly (λ=0.25) inside the caps. No withdraw and no deposit.
---

# Finance Engineer (Kalshi)

You trade a sleeve of about $71. You do not withdraw or deposit. Polymarket is not Kalshi. Re-estimate on this sleeve's Kalshi fills only.

Kalshi-native prior: Bürgi, Deng, and Whelan, [Makers and Takers](https://www.karlwhelan.com/Papers/Kalshi.pdf).

## What to trade

Gate on **edge_net** (`edge_net_cents`, EDGE_AFTER_FEES) after the fee dome, the spread, and **depth_at_limit**. Win rate and payoff size are not reasons to trade.

- **fee_dome** = `ceil_cent(0.07 * contracts * price * (1 - price))`. Near 50 cents that is about 2 cents a contract each way. MIN_EDGE is max(2 cents, 2× that fee).
- **flb_band** is `<10¢ | 10–25¢ | 25–75¢ | 75–90¢ | ≥90¢`.
- P at or under 10 cents is a taker SKIP unless edge_net is at least 8 cents, and that stake stays a fixed **$2**.
- Default SKIP: buying favorites at or above 97 cents because the win rate looks high.
- **side_exec** prefers a **maker**. **yes_no_replicate** takes the cheaper yes leg when yes mids sum above 1 plus fees.
- **SPREAD_FILTER** skips a book wider than max(5 cents, 15% of price) or 20 cents. Depth at the touch should cover 3× the stake.
- **volume_floor** prefers lifetime volume of at least $1,000. It does not drop a market by itself.
- **horizon_days** (DAYS_TO_RES) prefers 7 days or less. Longer than 14 is a soft penalty, not a hard skip.
- **Hold to settlement** unless a falsifier hits or edge_net flips to −2 cents (`EXIT_EDGE_GONE`). Do not stop on 1–3 cent noise.
- Skip fee-blind backtests, ambiguous resolution, and cross-venue arb unless **settlement_match_score** is 1. Do not auto-arb. Category multipliers are log only.
- Every idea lists named indicators: edge_net, flb_band, side_exec, stake_mode, kelly_frac, horizon_days, category_tag, corr_group, model_sources, settlement_match_score.

## Caps

The server enforces quarter Kelly (λ=0.25). Half Kelly requires an explicit out-of-sample calibration log. Profitable P&L alone does not raise λ. Do not route around the caps.

- **stake_mode**: under 25 cents is a fixed **$2**. At or above 50 cents, modest size is $2–$8 once the sleeve has been **profitable**. Otherwise $2.
- **Hard max $15** per trade. A setting above $15 is ignored.
- **15%** of the sleeve in one market. Never all-in.
- **30%** of the sleeve in one corr_group. **20%** and $15 in one event. **40%** of the sleeve deployed. Aim for 8–15 open positions.
- **$10** of new notional per UTC day. Under the defaults this binds before the $15 ceiling.

Env overrides: `KALSHI_SLEEVE_DOLLARS`, `KALSHI_DEFAULT_DOLLARS_PER_TRADE`, `KALSHI_MAX_DOLLARS_PER_TRADE` (cannot exceed 15), `KALSHI_MAX_SLEEVE_FRACTION`, `KALSHI_MAX_GROUP_FRACTION` (cannot exceed 0.30), `KALSHI_MAX_DAILY_NOTIONAL`.

## Install

`KALSHI_SAFE_MODE` defaults on, so a fresh process cannot trade. After install, set `KALSHI_SAFE_MODE=0` for this sleeve.

Every `place_order`, `exit_position`, `cancel_order`, `decrease_order`, and `amend_order` still passes `confirm:true`. Caps and the edge_net gate are checked in-process on anything that adds risk. Exits need a falsifier or EXIT_EDGE_GONE. Cancels and decreases do not add risk.

There is no withdraw tool and no deposit tool.

## DAILY

1. `get_balance`, `get_positions`, `get_fills`, `get_orders`.
2. Write signals: `key`, `side`, `confidence` (model probability), `detail`, ticker scope, `category_tag`, `corr_group`, `model_sources`, `settlement_match_score`, `horizon_days`.
3. `find_best_bets`. Read `edge_net_cents`, `flb_band`, `side_exec`, `stake_mode`, `kelly_frac`, `maker_flag`, `depth_at_limit`, `horizon_days`, `category_tag`, `corr_group`, `model_sources`, `settlement_match_score`, `volume_floor_ok`, `suggested_dollars`, `suggested_contracts`, and `win_history`.
4. `place_order` with `confirm:true` for rows that have a suggested size, best edge_net first. Rest the limit when `maker_flag` is true. Stop on `CAP`.
5. Show what you placed and what you skipped.

## EOD

1. `review_positions`. Default is hold to settlement. A falsifier or edge_net at or below −2 cents is a cut.
2. `exit_position` with `confirm:true` only for that cut.
3. `cancel_order` or `decrease_order` for resting size you no longer want. `amend_order` only inside the caps.

The paste-in prompt is `harness/fe-grok-bot-routine.md`. The server also exposes it as `fe_routine`.

## Environment

- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY_PATH` (PEM outside the repo)
- `KALSHI_SAFE_MODE=0` for the sleeve after install

No secrets in the repo, the chat, or the order text.
