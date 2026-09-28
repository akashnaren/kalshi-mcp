/**
 * Research gates. Rank and place on edge_net_cents (EDGE_AFTER_FEES)
 * after the Kalshi fee dome, spread, and depth.
 * Kalshi-native prior: Bürgi, Deng, and Whelan,
 * https://www.karlwhelan.com/Papers/Kalshi.pdf
 * Win rate and payoff size are not gates. Polymarket fills are not Kalshi fills.
 * Category multipliers are not encoded.
 */

export const FEE_RATE = 0.07;
export const MIN_EDGE_FLOOR_CENTS = 2;
export const LONGSHOT_PRICE = 0.1;
export const LONGSHOT_EDGE_CENTS = 8;
export const FIXED_STAKE_PRICE = 0.25;
export const MODEST_PRICE = 0.5;
export const FIXED_STAKE_DOLLARS = 2;
export const MODEST_CAP_DOLLARS = 8;
export const KELLY_CEILING_DOLLARS = 15;
export const FAVORITE_WR = 0.97;
export const KELLY_QUARTER = 0.25;
export const KELLY_HALF = 0.5;
export const EXIT_EDGE_GONE_CENTS = -2;
export const SPREAD_ABS_CAP = 0.2;
export const SPREAD_FLOOR = 0.05;
export const SPREAD_OF_PRICE = 0.15;
export const MIN_DEPTH_DOLLARS = 6;
export const DEPTH_STAKE_MULTIPLE = 3;
export const VOLUME_FLOOR_DOLLARS = 1000;
export const BANKROLL_UTIL_MAX = 0.4;
export const MAX_OPEN_POSITIONS = 15;
export const EVENT_FRACTION = 0.2;
export const EVENT_DOLLAR_CAP = 15;
export const DAYS_TO_RES_PREFER = 7;
export const DAYS_TO_RES_SOFT = 14;

export const CATEGORIES = Object.freeze(["Politics", "Crypto", "Sports", "Weather", "Finance", "Macro"]);

const FEE_BLIND_RE = /fee[-\s]?blind|without fees|no fees|ignoring fees/i;

export function feeDomeCents(price, contracts = 1) {
  const p = Number(price);
  const n = Number(contracts);
  if (!(p > 0) || !(p < 1) || !(n > 0)) return 0;
  const rawCents = FEE_RATE * n * p * (1 - p) * 100;
  return Math.ceil(rawCents - 1e-9);
}

export function minEdgeCents(price) {
  return Math.max(MIN_EDGE_FLOOR_CENTS, 2 * feeDomeCents(price, 1));
}

export function flbBand(price) {
  const p = Number(price);
  if (p <= LONGSHOT_PRICE) return "<10¢";
  if (p < FIXED_STAKE_PRICE) return "10–25¢";
  if (p < 0.75) return "25–75¢";
  if (p < 0.9) return "75–90¢";
  return "≥90¢";
}

export function stakeModeFor(price) {
  const p = Number(price);
  if (!(p > 0) || p < FIXED_STAKE_PRICE) return "fixed_2";
  if (p >= MODEST_PRICE) return "modest";
  return "kelly";
}

export function stakeCapDollars(mode, history) {
  if (mode === "fixed_2") return FIXED_STAKE_DOLLARS;
  if (mode === "modest") return history?.allows_scale ? MODEST_CAP_DOLLARS : FIXED_STAKE_DOLLARS;
  return history?.allows_scale ? KELLY_CEILING_DOLLARS : FIXED_STAKE_DOLLARS;
}

export function spreadLimit(price) {
  return Math.max(SPREAD_FLOOR, SPREAD_OF_PRICE * Number(price));
}

export function spreadTooWide(bid, ask) {
  if (!(Number(bid) > 0) || !(Number(ask) > 0)) return false;
  const spread = Number(ask) - Number(bid);
  if (spread > SPREAD_ABS_CAP) return true;
  return spread > spreadLimit(ask);
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

export function fractionalKellyDollars({
  p,
  price,
  bankroll,
  groupAlreadyOpen = false,
  drawdown = 0,
  oosCalibration = false,
}) {
  const full = kellyFull(p, price);
  if (!(full > 0)) return 0;
  const frac = oosCalibration === true ? KELLY_HALF : KELLY_QUARTER;
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
 * Per-contract edge_net_cents after fee dome and half-spread.
 * P<=0.10 is a taker skip unless edge is at least 8 cents, and that size stays $2.
 */
export function evaluateEntry({
  pModel,
  ask,
  bid = null,
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
  const horizonBump = Math.max(0, days - DAYS_TO_RES_SOFT) * 0.05;
  const mode = Number.isFinite(price) ? stakeModeFor(price) : "fixed_2";
  const rtCents = feeEntry + feeDomeCents(price, 1);
  const daysPrefer = days <= DAYS_TO_RES_PREFER;
  const base = {
    flb_band: band,
    fee_entry_cents: feeEntry,
    fee_exit_cents: feeExit,
    fee_dome: "ceil_cent(0.07 * contracts * price * (1 - price))",
    spread_rel: rel,
    liquidity_penalty_cents: liquidityPenalty,
    depth_at_limit: depthAtLimit,
    edge_net_cents: edge,
    edge_after_fees: edge,
    horizon_days: days,
    days_to_res: days,
    days_to_res_prefer: daysPrefer,
    hold_to_settle: holdToSettle !== false,
    hold_to_res_default: daysPrefer && rtCents > edge,
    maker_flag: true,
    side_exec: "maker",
    stake_mode: mode,
    kelly_frac: KELLY_QUARTER,
    category_edge: "log_only",
  };
  if (!(settlementMatch === 1)) return fail("ambiguous_settlement", base);
  if (!(price > 0) || !(price < 1) || !(p > 0) || p > 1) return fail("thin_edge", base);
  if (price >= FAVORITE_WR) return fail("favorite_wr", base);
  if (bidKnown && spreadTooWide(bid, price)) return fail("wide_spread", base);
  if (depthAtLimit > 0 && round4(depthAtLimit * price) < MIN_DEPTH_DOLLARS) return fail("thin_book", base);
  const need = minEdgeCents(price) + horizonBump;
  if (price <= LONGSHOT_PRICE && edge < LONGSHOT_EDGE_CENTS + horizonBump) {
    return fail("longshot_take", base);
  }
  if (edge < need) return fail("thin_edge", base);
  const taker = edge >= LONGSHOT_EDGE_CENTS && price > LONGSHOT_PRICE;
  return {
    ok: true,
    reason: null,
    ...base,
    maker_flag: !taker,
    side_exec: taker ? "taker" : "maker",
    threshold_cents: need,
  };
}

export function assertEarlyExit({ falsifierHit, edgeNetCents }) {
  if (falsifierHit === true) return { ok: true, reason: "falsifier" };
  if (edgeNetCents != null && edgeNetCents !== "" && Number(edgeNetCents) <= EXIT_EDGE_GONE_CENTS) {
    return { ok: true, reason: "edge_net flipped to EXIT_EDGE_GONE" };
  }
  return {
    ok: false,
    reason: "hold to settlement; no stop on 1–3¢ noise without an info change",
  };
}
