import { loadCaps, loadRiskBook, marketCapDollars, sizeIdeas } from "./caps.js";
import { planMarketQueries, policySummary, rankMarkets, resolveFilters, validateSignals } from "./policy.js";

/**
 * Read-only scan. Quotes come from the market list, so this does not fan out
 * into one order-book request per market. Sizing is advisory. The order
 * tools check the caps again before anything is sent.
 */
export async function findBestBets({ client, signals, options = {}, caps, ledger, now }) {
  const parsed = validateSignals(signals);
  const filters = resolveFilters(options);
  const queries = planMarketQueries(parsed);
  const markets = await fetchMarkets(client, queries, filters);
  const ranked = rankMarkets(markets, parsed, filters);
  const activeCaps = caps ?? loadCaps();
  const book = await suggestionBook(client, ledger, options, now);
  const sized = sizeIdeas(ranked.recommendations, {
    caps: activeCaps,
    marketExposure: book.marketExposure,
    dailyNotional: book.dailyNotional,
  });
  return {
    policy: policySummary(filters),
    caps: {
      sleeve_dollars: activeCaps.sleeve_dollars,
      max_dollars_per_idea: activeCaps.max_dollars_per_idea,
      max_sleeve_fraction: activeCaps.max_sleeve_fraction,
      max_daily_notional: activeCaps.max_daily_notional,
      market_cap: marketCapDollars(activeCaps),
      likely_size_fraction: activeCaps.likely_size_fraction,
    },
    filters: {
      min_confidence: filters.min_confidence,
      min_volume_24h: filters.min_volume_24h,
      min_open_interest: filters.min_open_interest,
      max_spread: filters.max_spread,
      min_top_size: filters.min_top_size,
      max_pages: filters.max_pages,
      limit: filters.limit,
    },
    query_count: queries.length,
    scanned_markets: markets.length,
    book_error: book.book_error,
    recommendations: sized.recommendations.slice(0, filters.limit),
    recommendation_count: sized.recommendations.length,
    unsized: sized.unsized.slice(0, filters.limit),
    skipped: ranked.skipped,
    skip_examples: ranked.examples,
  };
}

async function suggestionBook(client, ledger, options, now) {
  if (typeof client.getPositions !== "function") {
    return {
      marketExposure: options.market_exposure ?? {},
      dailyNotional: Number(options.daily_notional) || 0,
      book_error: null,
    };
  }
  try {
    const book = await loadRiskBook(client, ledger, now);
    return { marketExposure: book.byTicker, dailyNotional: book.daily, book_error: null };
  } catch (err) {
    return {
      marketExposure: options.market_exposure ?? {},
      dailyNotional: Number(options.daily_notional) || 0,
      book_error: err.message,
    };
  }
}

async function fetchMarkets(client, queries, filters) {
  const byTicker = new Map();
  for (const query of queries) {
    let cursor = "";
    const pageLimit = query.tickers
      ? Math.min(filters.page_limit, query.tickers.split(",").length)
      : filters.page_limit;
    for (let page = 0; page < filters.max_pages; page += 1) {
      const response = await client.getMarkets({
        ...query,
        limit: pageLimit,
        cursor: cursor || undefined,
      });
      const batch = response?.markets ?? [];
      for (const market of batch) {
        if (market?.ticker && !byTicker.has(market.ticker)) byTicker.set(market.ticker, market);
      }
      cursor = response?.cursor || "";
      if (!cursor || batch.length === 0) break;
    }
  }
  return [...byTicker.values()];
}
