/**
 * Research gates from the 2026-09-28 winning brief.
 * Rank and place on edge_net after the Kalshi fee dome, spread, and depth.
 * Win rate and payoff size are not gates. Polymarket fills are not Kalshi fills.
 */

export const FEE_RATE = 0.07;
export const TAKER_EDGE_CENTS = 4;
export const MAKER_EDGE_CENTS = 3;
export const SMALL_EDGE_CENTS = 6;
export const EXTREME_GAP = 0.2;
export const EXTREME_EDGE_CENTS = 10;
export const LONGSHOT_TAKE = 0.15;
export const POLITICS_LONGSHOT = 0.25;
export const FAVORITE_WR = 0.97;
export const KELLY_QUARTER = 0.25;
export const KELLY_HALF = 0.5;

export const CATEGORIES = Object.freeze(["Politics", "Crypto", "Sports", "Weather", "Finance", "Macro"]);

const FEE_BLIND_RE = /fee[-\s]?blind|without fees|no fees|ignoring fees/i;

export function feeDomeCents(price, contracts = 1) {
  const p = Number(price);
  const n = Number(contracts);
  if (!(p > 0) || !(p < 1) || !(n > 0)) return 0;
  const rawCents = FEE_RATE * n * p * (1 - p) * 100;
  return Math.ceil(rawCents - 1e-9);
}

export function flbBand(price) {
  const p = Number(price);
  if (p < 0.1) return "<10¢";
  if (p < 0.25) return "10–25¢";
  if (p < 0.75) return "25–75¢";
  if (p < 0.9) return "75–90¢";
  return "≥90¢";
}

export function spreadRel(bid, ask) {
  const b = Number(bid);
  const a = Number(ask);
  if (!(a > 0) || !(b > 0)) return null;
  const mid = (a + b) / 2;
  if (!(mid > 0)) return null;
  return Math.round(((a - b) / mid) * 10000) / 10000;
}

export function kellyFull(p, price) {
  const prob = Number(p);
  const m = Number(price);
  if (!(m > 0) || !(m < 1) || !(prob > m) || prob > 1) return 0;
  return (prob - m) / (1 - m);
}

export function drawdownScale(drawdown) {
  const d = Number(drawdown) || 0;
  if (d >= 0.4) return 0;
  if (d >= 0.2) return 0.5;
  return 1;
}

function round4(value) {
  return Math.round((value + Number.EPSILON) * 10000) / 10000;
}

export function fractionalKellyDollars({ p, price, bankroll, history, groupAlreadyOpen = false, drawdown = 0 }) {
  const full = kellyFull(p, price);
  if (!(full > 0)) return 0;
  const frac = history?.allows_scale ? KELLY_HALF : KELLY_QUARTER;
  let dollars = full * frac * Number(bankroll);
  if (groupAlreadyOpen) dollars *= 0.5;
  dollars *= drawdownScale(drawdown);
  return round4(dollars);
}

export function isFeeBlind(text) {
  return FEE_BLIND_RE.test(String(text || ""));
}

export function horizonFromClose(closeTime, now = new Date()) {
  if (!closeTime) return null;
  const t = Date.parse(closeTime);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, Math.round((t - now.getTime()) / 86400000));
}

function fail(reason, extra = {}) {
  return { ok: false, reason, ...extra };
}

/**
 * Per-contract edge after fee dome, half-spread penalty, and hold-to-settle.
 * Returns maker_flag when the surviving edge is small.
 */
export function evaluateEntry({
  pModel,
  ask,
  bid = null,
  category = "Macro",
  horizonDays = 0,
  holdToSettle = true,
  depth = 0,
  settlementMatch = 1,
} = {}) {
  const p = Number(pModel);
  const price = Number(ask);
  const band = Number.isFinite(price) ? flbBand(price) : null;
  const depthAtLimit = Number(depth) || 0;
  const feeEntry = feeDomeCents(price, 1);
  const feeExit = holdToSettle ? 0 : feeDomeCents(price, 1);
  const rel = spreadRel(bid, price);
  const bidKnown = bid != null && bid !== "" && Number(bid) > 0;
  const spreadCents = bidKnown ? Math.max(0, (price - Number(bid)) * 100) : 0;
  const liquidityPenalty = Math.round(spreadCents * 0.5 * 100) / 100;
  const grossCents = Number.isFinite(p) && Number.isFinite(price) ? (p - price) * 100 : 0;
  const edge = Math.round((grossCents - feeEntry - feeExit - liquidityPenalty) * 100) / 100;
  const days = Number(horizonDays) || 0;
  const horizonBump = Math.max(0, days - 14) * 0.05;
  const base = {
    flb_band: band,
    fee_entry_cents: feeEntry,
    fee_exit_cents: feeExit,
    fee_dome: "ceil_cent(0.07 * contracts * price * (1 - price))",
    spread_rel: rel,
    liquidity_penalty_cents: liquidityPenalty,
    depth_at_limit: depthAtLimit,
    edge_net_cents: edge,
    horizon_days: days,
    hold_to_settle: holdToSettle !== false,
    maker_flag: false,
  };
  if (!(settlementMatch === 1)) return fail("ambiguous_settlement", base);
  if (!(price > 0) || !(price < 1) || !(p > 0) || p > 1) return fail("thin_edge", base);
  if (price >= FAVORITE_WR) return fail("favorite_wr", base);
  const longshotLine = category === "Politics" || category === "Crypto" ? POLITICS_LONGSHOT : LONGSHOT_TAKE;
  const gap = p - price;
  const extreme = gap >= EXTREME_GAP && edge >= EXTREME_EDGE_CENTS + horizonBump;
  if (price < longshotLine && !extreme) return fail("longshot_take", base);
  const makerNeed = MAKER_EDGE_CENTS + horizonBump;
  const takerNeed = TAKER_EDGE_CENTS + horizonBump;
  if (edge < makerNeed) return fail("thin_edge", base);
  const maker = price < longshotLine || edge < SMALL_EDGE_CENTS || edge < takerNeed;
  return {
    ok: true,
    reason: null,
    ...base,
    maker_flag: maker,
    threshold_cents: maker && edge < takerNeed ? makerNeed : takerNeed,
  };
}

export function assertEarlyExit({ falsifierHit, edgeNetCents, exitPrice }) {
  if (falsifierHit === true) return { ok: true, reason: "falsifier" };
  const fee = feeDomeCents(exitPrice, 1);
  if (edgeNetCents != null && edgeNetCents !== "" && Number(edgeNetCents) <= -fee) {
    return { ok: true, reason: "edge_flipped" };
  }
  return {
    ok: false,
    reason: "hold to settlement unless a falsifier hits or edge_net flips through the exit fee",
  };
}
