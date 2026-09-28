/**
 * Akash Finance Engineer policy.
 * Gate on edge_net after the Kalshi fee dome, spread, and depth.
 * Win rate and payoff size are not reasons to trade.
 * Caps and confirm:true are enforced on every order. No withdraw, no deposit.
 */

import {
  CATEGORIES,
  VOLUME_FLOOR_DOLLARS,
  evaluateEntry,
  feeDomeCents,
  horizonFromClose,
  isFeeBlind,
} from "./edge.js";

export const HIGH_CONFIDENCE = 0.65;
export const ASYMMETRIC_MAX_STAKE = 0.4;
export const LIKELY_CONFIDENCE = 0.85;

export const DEFAULTS = Object.freeze({
  min_confidence: HIGH_CONFIDENCE,
  min_volume_24h: 200,
  min_open_interest: 100,
  max_spread: 0.08,
  min_top_size: 10,
  max_pages: 3,
  page_limit: 200,
  limit: 8,
});

export const MAX_QUERIES = 8;
export const TICKERS_PER_QUERY = 50;
export const MAX_SIGNALS = 40;

const VAGUE_KEYS = new Set([
  "gut",
  "feeling",
  "hunch",
  "intuition",
  "vibe",
  "confidence",
  "likely",
  "yolo",
  "guess",
  "opinion",
  "instinct",
  "bullish",
  "bearish",
  "edge",
  "alpha",
  "signal",
  "indicator",
  "bet",
  "moon",
]);

const KEY_RE = /^[a-z][a-z0-9_]{2,48}$/;
const TICKER_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/;

const OFF_VALUES = new Set(["0", "false", "off", "no"]);

export class PolicyError extends Error {
  constructor(code, message) {
    super(message);
    this.name = "PolicyError";
    this.code = code;
  }
}

export function isSafeMode(env = process.env) {
  const raw = env.KALSHI_SAFE_MODE;
  if (raw == null || String(raw).trim() === "") return true;
  return !OFF_VALUES.has(String(raw).trim().toLowerCase());
}

export function assertCanMutate(confirm, env = process.env) {
  if (isSafeMode(env)) {
    throw new PolicyError(
      "SAFE_MODE",
      "SAFE_MODE is on (install default). No order was sent. The Finance Engineer sets KALSHI_SAFE_MODE=0 after install. confirm:true and the caps still apply.",
    );
  }
  if (confirm !== true) {
    throw new PolicyError(
      "CONFIRM_REQUIRED",
      "Mutation refused. Pass confirm:true on this call. Caps are checked in-process either way.",
    );
  }
}

export function round4(value) {
  return Math.round((value + Number.EPSILON) * 10000) / 10000;
}

export function parseDollars(value) {
  if (value == null || value === "") return null;
  const n = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isFinite(n)) return null;
  return round4(n);
}

export function parseCount(value) {
  if (value == null || value === "") return null;
  const n = typeof value === "number" ? value : Number(String(value).trim());
  if (!Number.isFinite(n)) return null;
  return n;
}

function readDollars(market, dollarKey, centKey) {
  const dollars = parseDollars(market[dollarKey]);
  if (dollars != null) return dollars;
  if (centKey == null || market[centKey] == null || market[centKey] === "") return null;
  const cents = Number(market[centKey]);
  if (!Number.isFinite(cents)) return null;
  return round4(cents / 100);
}

function readCount(market, ...keys) {
  for (const key of keys) {
    const n = parseCount(market[key]);
    if (n != null) return n;
  }
  return null;
}

export function validateSignals(signals) {
  if (!Array.isArray(signals) || signals.length === 0) {
    throw new PolicyError(
      "NO_SIGNALS",
      "At least one signal is required. Confidence without a named indicator is not confidence.",
    );
  }
  if (signals.length > MAX_SIGNALS) {
    throw new PolicyError("SCAN_TOO_WIDE", `At most ${MAX_SIGNALS} signals per scan.`);
  }
  return signals.map((signal, index) => validateSignal(signal, index));
}

function validateSignal(signal, index) {
  if (signal == null || typeof signal !== "object") {
    throw new PolicyError("BAD_KEY", `Signal ${index} is not an object.`);
  }
  const key = String(signal.key ?? "").trim();
  if (!KEY_RE.test(key)) {
    throw new PolicyError(
      "BAD_KEY",
      `Signal ${index} key "${key}" must be a concrete snake_case indicator, 3 to 49 characters.`,
    );
  }
  if (VAGUE_KEYS.has(key)) {
    throw new PolicyError(
      "VAGUE_KEY",
      `Signal key "${key}" is not a concrete indicator. Name the observation, for example rcp_polling_average or nhc_cone_includes_city.`,
    );
  }
  const confidence = Number(signal.confidence);
  if (!Number.isFinite(confidence) || confidence <= 0 || confidence > 1) {
    throw new PolicyError("BAD_KEY", `Signal "${key}" confidence must be greater than 0 and at most 1.`);
  }
  if (signal.side !== "yes" && signal.side !== "no") {
    throw new PolicyError("BAD_KEY", `Signal "${key}" side must be yes or no.`);
  }
  const detail = String(signal.detail ?? "").trim();
  if (detail.length < 12 || detail.length > 280) {
    throw new PolicyError(
      "BAD_DETAIL",
      `Signal "${key}" needs a concrete detail (12 to 280 characters) stating what was observed.`,
    );
  }
  const market_ticker = cleanTicker(signal.market_ticker, key, "market_ticker");
  const event_ticker = cleanTicker(signal.event_ticker, key, "event_ticker");
  const series_ticker = cleanTicker(signal.series_ticker, key, "series_ticker");
  if (!market_ticker && !event_ticker && !series_ticker) {
    throw new PolicyError(
      "NO_SCOPE",
      `Signal "${key}" needs a market_ticker, event_ticker, or series_ticker so the scan stays narrow.`,
    );
  }
  const category_tag = String(signal.category_tag ?? "").trim();
  if (!CATEGORIES.includes(category_tag)) {
    throw new PolicyError(
      "BAD_KEY",
      `Signal "${key}" category_tag must be one of ${CATEGORIES.join(", ")}.`,
    );
  }
  const corr_group = String(signal.corr_group ?? "").trim();
  if (!KEY_RE.test(corr_group)) {
    throw new PolicyError("BAD_KEY", `Signal "${key}" corr_group must be a snake_case risk driver.`);
  }
  const model_sources = String(signal.model_sources ?? "").trim();
  if (model_sources.length < 8 || model_sources.length > 200) {
    throw new PolicyError("BAD_KEY", `Signal "${key}" model_sources must name the model, 8 to 200 characters.`);
  }
  const settlement_match_score = Number(signal.settlement_match_score);
  if (!Number.isFinite(settlement_match_score) || settlement_match_score < 0 || settlement_match_score > 1) {
    throw new PolicyError("BAD_KEY", `Signal "${key}" settlement_match_score must be from 0 to 1.`);
  }
  let horizon_days = null;
  if (signal.horizon_days != null && signal.horizon_days !== "") {
    horizon_days = Number(signal.horizon_days);
    if (!Number.isFinite(horizon_days) || horizon_days < 0 || horizon_days > 3650) {
      throw new PolicyError("BAD_KEY", `Signal "${key}" horizon_days must be a non-negative number of days.`);
    }
  }
  const falsifier = signal.falsifier == null ? "" : String(signal.falsifier).trim();
  return {
    key,
    confidence: round4(confidence),
    side: signal.side,
    detail,
    market_ticker,
    event_ticker,
    series_ticker,
    category_tag,
    corr_group,
    model_sources,
    settlement_match_score: round4(settlement_match_score),
    horizon_days,
    falsifier,
  };
}

function cleanTicker(value, key, field) {
  if (value == null || value === "") return "";
  const ticker = String(value).trim();
  if (!TICKER_RE.test(ticker)) {
    throw new PolicyError("BAD_KEY", `Signal "${key}" ${field} is not a ticker.`);
  }
  return ticker;
}

export function resolveFilters(options = {}) {
  const requested = options.min_confidence;
  return {
    min_confidence: requested == null ? 0 : bounded(requested, 0, 0, 1, "min_confidence"),
    min_volume_24h: nonNegative(options.min_volume_24h, DEFAULTS.min_volume_24h, "min_volume_24h"),
    min_open_interest: nonNegative(options.min_open_interest, DEFAULTS.min_open_interest, "min_open_interest"),
    max_spread: bounded(options.max_spread, DEFAULTS.max_spread, 0, 1, "max_spread"),
    min_top_size: nonNegative(options.min_top_size, DEFAULTS.min_top_size, "min_top_size"),
    max_pages: Math.round(bounded(options.max_pages, DEFAULTS.max_pages, 1, 5, "max_pages")),
    page_limit: DEFAULTS.page_limit,
    limit: Math.round(bounded(options.limit, DEFAULTS.limit, 1, 25, "limit")),
  };
}

function nonNegative(value, fallback, name) {
  return bounded(value, fallback, 0, 1_000_000_000, name);
}

function bounded(value, fallback, lo, hi, name) {
  if (value == null || value === "") return fallback;
  const n = Number(value);
  if (!Number.isFinite(n) || n < lo || n > hi) {
    throw new PolicyError("BAD_FILTER", `${name} must be between ${lo} and ${hi}.`);
  }
  return n;
}

export function planMarketQueries(signals) {
  const tickers = new Set();
  const events = new Set();
  const series = new Set();
  for (const signal of signals) {
    if (signal.market_ticker) tickers.add(signal.market_ticker);
    else if (signal.event_ticker) events.add(signal.event_ticker);
    else series.add(signal.series_ticker);
  }
  const queries = [];
  const tickerList = [...tickers].sort();
  for (let i = 0; i < tickerList.length; i += TICKERS_PER_QUERY) {
    queries.push({
      tickers: tickerList.slice(i, i + TICKERS_PER_QUERY).join(","),
      status: "open",
      mve_filter: "exclude",
    });
  }
  for (const event_ticker of [...events].sort()) {
    queries.push({ event_ticker, status: "open", mve_filter: "exclude" });
  }
  for (const series_ticker of [...series].sort()) {
    queries.push({ series_ticker, status: "open", mve_filter: "exclude" });
  }
  if (queries.length > MAX_QUERIES) {
    throw new PolicyError(
      "SCAN_TOO_WIDE",
      `This scan would call /markets ${queries.length} times (max ${MAX_QUERIES}). Narrow the signals.`,
    );
  }
  return queries;
}

const TRADABLE = new Set(["active", "open"]);

export function rankMarkets(markets, signals, filters) {
  const skipped = {
    inactive: 0,
    not_binary: 0,
    no_signal: 0,
    conflict: 0,
    low_confidence: 0,
    no_ask: 0,
    illiquid: 0,
    wide_spread: 0,
    thin_book: 0,
    not_in_lane: 0,
    longshot_take: 0,
    favorite_wr: 0,
    thin_edge: 0,
    ambiguous_settlement: 0,
    fee_blind: 0,
    research_conflict: 0,
    yes_no_replicate: 0,
  };
  const eventLegs = new Map();
  const examples = [];
  const note = (ticker, reason) => {
    skipped[reason] += 1;
    if (examples.length < 8 && reason !== "no_signal") examples.push({ ticker, reason });
  };

  const recommendations = [];
  for (const market of markets) {
    const ticker = market?.ticker ?? "";
    noteYesLeg(eventLegs, market);
    if (market?.status && !TRADABLE.has(market.status)) {
      note(ticker, "inactive");
      continue;
    }
    if ((market?.market_type && market.market_type !== "binary") || market?.mve_collection_ticker) {
      note(ticker, "not_binary");
      continue;
    }

    const yesSignals = [];
    const noSignals = [];
    for (const signal of signals) {
      if (!signalMatches(signal, market)) continue;
      if (signal.side === "yes") yesSignals.push(signal);
      else noSignals.push(signal);
    }
    if (yesSignals.length === 0 && noSignals.length === 0) {
      skipped.no_signal += 1;
      continue;
    }
    if (yesSignals.length > 0 && noSignals.length > 0) {
      note(ticker, "conflict");
      continue;
    }

    const group = yesSignals.length > 0 ? yesSignals : noSignals;
    const side = yesSignals.length > 0 ? "yes" : "no";
    const confidence = round4(Math.max(...group.map((signal) => signal.confidence)));
    if (confidence < filters.min_confidence) {
      note(ticker, "low_confidence");
      continue;
    }
    const research = mergeResearch(group);
    if (research.conflict) {
      note(ticker, "research_conflict");
      continue;
    }

    const quote = quoteForSide(market, side);
    if (quote.ask == null || !(quote.ask > 0) || !(quote.ask < quote.notional)) {
      note(ticker, "no_ask");
      continue;
    }
    const volume = readCount(market, "volume_24h_fp", "volume_24h", "volume_fp", "volume");
    const openInterest = readCount(market, "open_interest_fp", "open_interest");
    if (volume == null || volume < filters.min_volume_24h || openInterest == null || openInterest < filters.min_open_interest) {
      note(ticker, "illiquid");
      continue;
    }
    if (quote.bid == null || !(quote.bid > 0)) {
      note(ticker, "wide_spread");
      continue;
    }
    const spread = round4(quote.ask - quote.bid);
    if (spread < 0 || spread > filters.max_spread) {
      note(ticker, "wide_spread");
      continue;
    }
    if (quote.topSize == null || quote.topSize < filters.min_top_size) {
      note(ticker, "thin_book");
      continue;
    }

    const stake = quote.ask;
    const payout = round4(quote.notional - stake);
    if (isFeeBlind(`${research.model_sources} ${group.map((signal) => signal.detail).join(" ")}`)) {
      note(ticker, "fee_blind");
      continue;
    }
    const horizonDays = research.horizon_days ?? horizonFromClose(market.close_time) ?? 30;
    const decision = evaluateEntry({
      pModel: confidence,
      ask: stake,
      bid: quote.bid,
      category: research.category_tag,
      horizonDays,
      holdToSettle: true,
      depth: quote.topSize,
      settlementMatch: research.settlement_match_score,
    });
    if (!decision.ok) {
      note(ticker, decision.reason);
      continue;
    }
    const keys = [...new Set(group.map((signal) => signal.key))].sort();
    recommendations.push({
      ticker,
      event_ticker: market.event_ticker ?? "",
      label: (side === "yes" ? market.yes_sub_title : market.no_sub_title) || market.title || ticker,
      side,
      lane: decision.maker_flag ? "maker" : "taker",
      confidence,
      keys,
      indicators: group.map((signal) => ({
        key: signal.key,
        confidence: signal.confidence,
        detail: signal.detail,
      })),
      stake,
      payout,
      payout_to_stake: round4(payout / stake),
      score: decision.edge_net_cents,
      edge_net_cents: decision.edge_net_cents,
      fee_entry_cents: decision.fee_entry_cents,
      fee_exit_cents: decision.fee_exit_cents,
      fee_dome: decision.fee_dome,
      flb_band: decision.flb_band,
      spread_rel: decision.spread_rel,
      depth_at_limit: decision.depth_at_limit,
      maker_flag: decision.maker_flag,
      hold_to_settle: true,
      horizon_days: horizonDays,
      category_tag: research.category_tag,
      category_edge: "log_only",
      corr_group: research.corr_group,
      model_sources: research.model_sources,
      settlement_match_score: research.settlement_match_score,
      falsifier: research.falsifier,
      side_exec: decision.side_exec,
      stake_mode: decision.stake_mode,
      kelly_frac: decision.kelly_frac,
      edge_after_fees: decision.edge_net_cents,
      hold_to_res_default: decision.hold_to_res_default,
      days_to_res: horizonDays,
      days_to_res_prefer: decision.days_to_res_prefer,
      yes_no_replicate: "clear",
      volume_floor_ok: dollarVolume(market, quote) >= VOLUME_FLOOR_DOLLARS,
      close_time: market.close_time ?? null,
      liquidity: {
        volume_24h: volume,
        open_interest: openInterest,
        spread,
        top_size: quote.topSize,
      },
    });
  }

  const kept = applyYesNoReplicate(recommendations, eventLegs, note);
  kept.sort((a, b) => {
    if (b.edge_net_cents !== a.edge_net_cents) return b.edge_net_cents - a.edge_net_cents;
    if (a.volume_floor_ok !== b.volume_floor_ok) return a.volume_floor_ok ? -1 : 1;
    if (a.stake !== b.stake) return a.stake - b.stake;
    return a.ticker < b.ticker ? -1 : a.ticker > b.ticker ? 1 : 0;
  });

  return { recommendations: kept, skipped, examples };
}

function noteYesLeg(eventLegs, market) {
  const event = market?.event_ticker;
  if (!event || market?.market_type && market.market_type !== "binary") return;
  const bid = readDollars(market, "yes_bid_dollars", "yes_bid");
  const ask = readDollars(market, "yes_ask_dollars", "yes_ask");
  if (!(bid > 0) || !(ask > 0) || !(ask < 1)) return;
  const mid = (bid + ask) / 2;
  const legs = eventLegs.get(event) ?? [];
  if (!legs.some((leg) => leg.ticker === market.ticker)) {
    legs.push({ ticker: market.ticker, mid, fee: feeDomeCents(mid, 1) / 100 });
    eventLegs.set(event, legs);
  }
}

function applyYesNoReplicate(recommendations, eventLegs, note) {
  const dominated = new Set();
  const cheaper = new Set();
  for (const legs of eventLegs.values()) {
    if (legs.length < 2) continue;
    const sumMid = legs.reduce((sum, leg) => sum + leg.mid, 0);
    const sumFee = legs.reduce((sum, leg) => sum + leg.fee, 0);
    if (!(sumMid > 1 + sumFee)) continue;
    const best = legs.reduce((left, right) => (left.mid <= right.mid ? left : right));
    cheaper.add(best.ticker);
    for (const leg of legs) {
      if (leg.ticker !== best.ticker) dominated.add(leg.ticker);
    }
  }
  const kept = [];
  for (const row of recommendations) {
    if (row.side === "yes" && dominated.has(row.ticker)) {
      note(row.ticker, "yes_no_replicate");
      continue;
    }
    kept.push({
      ...row,
      yes_no_replicate: row.side === "yes" && cheaper.has(row.ticker) ? "cheaper_leg" : "clear",
    });
  }
  return kept;
}

function dollarVolume(market, quote) {
  const lifetime = readCount(market, "volume_fp", "volume");
  const day = readCount(market, "volume_24h_fp", "volume_24h");
  const contracts = lifetime ?? day ?? 0;
  const mid = quote.bid > 0 ? (quote.bid + quote.ask) / 2 : quote.ask;
  return round4(contracts * mid);
}

function quoteForSide(market, side) {
  const notional = readDollars(market, "notional_value_dollars", "notional_value") ?? 1;
  if (side === "yes") {
    return {
      notional,
      bid: readDollars(market, "yes_bid_dollars", "yes_bid"),
      ask: readDollars(market, "yes_ask_dollars", "yes_ask"),
      topSize: readCount(market, "yes_ask_size_fp", "yes_ask_size"),
    };
  }
  return {
    notional,
    bid: readDollars(market, "no_bid_dollars", "no_bid"),
    ask: readDollars(market, "no_ask_dollars", "no_ask"),
    topSize: readCount(market, "no_ask_size_fp", "no_ask_size", "yes_bid_size_fp", "yes_bid_size"),
  };
}

export function signalMatches(signal, market) {
  if (signal.market_ticker) return signal.market_ticker === market.ticker;
  if (signal.event_ticker) return signal.event_ticker === market.event_ticker;
  const series = signal.series_ticker;
  if (!series) return false;
  const eventTicker = market.event_ticker ?? "";
  const ticker = market.ticker ?? "";
  return eventTicker === series || eventTicker.startsWith(`${series}-`) || ticker.startsWith(`${series}-`);
}

function mergeResearch(group) {
  const categories = new Set(group.map((signal) => signal.category_tag));
  const groups = new Set(group.map((signal) => signal.corr_group));
  if (categories.size > 1 || groups.size > 1) return { conflict: true };
  const horizons = group.map((signal) => signal.horizon_days).filter((value) => value != null);
  return {
    conflict: false,
    category_tag: [...categories][0],
    corr_group: [...groups][0],
    model_sources: [...new Set(group.map((signal) => signal.model_sources))].join("; "),
    settlement_match_score: Math.min(...group.map((signal) => signal.settlement_match_score)),
    horizon_days: horizons.length ? Math.max(...horizons) : null,
    falsifier: group.map((signal) => signal.falsifier).filter(Boolean).join("; "),
  };
}

export function classifyLane({ confidence, stake, minConfidence = HIGH_CONFIDENCE }) {
  if (confidence >= minConfidence && stake <= ASYMMETRIC_MAX_STAKE) return "asymmetric";
  if (confidence >= LIKELY_CONFIDENCE) return "likely";
  return null;
}

function laneRank(lane) {
  return lane === "asymmetric" ? 0 : 1;
}

export function policySummary(filters) {
  return {
    name: "akash_finance_engineer",
    prefer: "edge_net_cents after fee dome, spread, and depth; maker when that edge is small; hold to settlement. Prior: Bürgi, Deng, and Whelan, https://www.karlwhelan.com/Papers/Kalshi.pdf",
    score: "edge_net_cents",
    fee_dome: "ceil_cent(0.07 * contracts * price * (1 - price))",
    size: "quarter Kelly (λ=0.25) inside stake_mode caps; half Kelly only with an explicit OOS calibration log",
    kelly_frac: 0.25,
    bankroll_util_max: 0.4,
    max_open_positions: 15,
    category_edge: "log_only",
    cross_venue_lead: "optional; do not auto-arb",
    payout: "profit if that side wins (notional minus the ask)",
    stake: "ask paid to enter, in dollars",
    confidence_min_applied: filters.min_confidence,
    requires_named_indicator_keys: true,
    named_indicators: [
      "edge_net_cents",
      "flb_band",
      "side_exec",
      "yes_no_replicate",
      "kelly_frac",
      "stake_mode",
      "bankroll_util",
      "spread_rel",
      "volume_floor",
      "horizon_days",
      "category_tag",
      "corr_group",
      "model_sources",
      "settlement_match_score",
      "hold_to_res_default",
      "exit_edge_gone",
    ],
    polymarket_is_not_kalshi: true,
    auto_trade: true,
    scan_places_orders: false,
    mode: "trade_within_caps",
    withdraw: false,
    deposit: false,
  };
}
