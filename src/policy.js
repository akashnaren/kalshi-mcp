/**
 * Akash Finance Engineer policy.
 * Recommend a side only when confidence that it wins is high, and that
 * confidence is backed by named indicator keys. Among those, prefer low
 * stake and high payout. Score = confidence * payout / stake.
 * Recommend only. Never auto-trade.
 */

export const HIGH_CONFIDENCE = 0.65;

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
      "SAFE_MODE is on (default). No order was sent. Leave KALSHI_SAFE_MODE=1. This server recommends; it does not auto-trade.",
    );
  }
  if (confirm !== true) {
    throw new PolicyError(
      "CONFIRM_REQUIRED",
      "Mutation refused. Pass confirm:true on this call. find_best_bets never does that for you.",
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
  return {
    key,
    confidence: round4(confidence),
    side: signal.side,
    detail,
    market_ticker,
    event_ticker,
    series_ticker,
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
  if (requested != null && Number(requested) < HIGH_CONFIDENCE) {
    throw new PolicyError(
      "CONFIDENCE_FLOOR",
      `min_confidence cannot be below ${HIGH_CONFIDENCE}. High confidence is required before a side is recommended.`,
    );
  }
  return {
    min_confidence: requested == null ? DEFAULTS.min_confidence : Number(requested),
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
  };
  const examples = [];
  const note = (ticker, reason) => {
    skipped[reason] += 1;
    if (examples.length < 8 && reason !== "no_signal") examples.push({ ticker, reason });
  };

  const recommendations = [];
  for (const market of markets) {
    const ticker = market?.ticker ?? "";
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
    const score = round4((confidence * payout) / stake);
    const keys = [...new Set(group.map((signal) => signal.key))].sort();
    recommendations.push({
      ticker,
      event_ticker: market.event_ticker ?? "",
      label: (side === "yes" ? market.yes_sub_title : market.no_sub_title) || market.title || ticker,
      side,
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
      score,
      close_time: market.close_time ?? null,
      liquidity: {
        volume_24h: volume,
        open_interest: openInterest,
        spread,
        top_size: quote.topSize,
      },
    });
  }

  recommendations.sort((a, b) => {
    if (b.score !== a.score) return b.score - a.score;
    if (b.payout_to_stake !== a.payout_to_stake) return b.payout_to_stake - a.payout_to_stake;
    if (a.stake !== b.stake) return a.stake - b.stake;
    return a.ticker < b.ticker ? -1 : a.ticker > b.ticker ? 1 : 0;
  });

  return { recommendations, skipped, examples };
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

export function policySummary(filters) {
  return {
    name: "akash_finance_engineer",
    prefer: "low stake and high payout, only when confidence that the side wins is high",
    score: "confidence * payout / stake",
    payout: "profit if that side wins (notional minus the ask)",
    stake: "ask paid to enter, in dollars",
    high_confidence_min: HIGH_CONFIDENCE,
    confidence_min_applied: filters.min_confidence,
    requires_named_indicator_keys: true,
    auto_trade: false,
    mode: "recommend_only",
  };
}
