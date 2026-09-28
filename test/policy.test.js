import assert from "node:assert/strict";
import test from "node:test";
import {
  HIGH_CONFIDENCE,
  assertCanMutate,
  isSafeMode,
  planMarketQueries,
  rankMarkets,
  resolveFilters,
  validateSignals,
} from "../src/policy.js";
import { filters, market, signal } from "./helpers.js";

test("safe mode is on unless explicitly disabled", () => {
  assert.equal(isSafeMode({}), true);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "" }), true);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "1" }), true);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "true" }), true);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "yes" }), true);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "0" }), false);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "false" }), false);
  assert.equal(isSafeMode({ KALSHI_SAFE_MODE: "off" }), false);
});

test("mutations need safe mode off and confirm true", () => {
  assert.throws(() => assertCanMutate(true, { KALSHI_SAFE_MODE: "1" }), /SAFE_MODE/);
  assert.throws(() => assertCanMutate(undefined, {}), /SAFE_MODE/);
  assert.throws(() => assertCanMutate(false, { KALSHI_SAFE_MODE: "0" }), /confirm:true/);
  assert.throws(() => assertCanMutate("true", { KALSHI_SAFE_MODE: "0" }), /confirm:true/);
  assert.doesNotThrow(() => assertCanMutate(true, { KALSHI_SAFE_MODE: "0" }));
});

test("the edge gate replaces a confidence floor", () => {
  assert.equal(HIGH_CONFIDENCE, 0.65);
  assert.equal(resolveFilters({}).min_confidence, 0);
  assert.equal(resolveFilters({ min_confidence: 0.4 }).min_confidence, 0.4);
});

test("signals require a concrete named key, detail, and scope", () => {
  assert.throws(() => validateSignals([]), /named indicator/);
  assert.throws(() => validateSignals([signal({ key: "gut" })]), /VAGUE_KEY|not a concrete/);
  assert.throws(() => validateSignals([signal({ key: "Feeling" })]), /snake_case/);
  assert.throws(() => validateSignals([signal({ detail: "looks good" })]), /concrete detail/);
  assert.throws(() => validateSignals([signal({ market_ticker: "", event_ticker: "", series_ticker: "" })]), /market_ticker/);
  assert.throws(() => validateSignals([signal({ category_tag: "Vibes" })]), /category_tag/);
  assert.throws(() => validateSignals([signal({ settlement_match_score: 2 })]), /settlement_match_score/);
  const [parsed] = validateSignals([signal()]);
  assert.equal(parsed.key, "nhc_cone_includes_city");
  assert.deepEqual(parsed.keys ?? undefined, undefined);
});

test("higher edge_net outranks a larger payoff", () => {
  const cheap = market({
    ticker: "CHEAP-1",
    yes_bid_dollars: "0.1400",
    yes_ask_dollars: "0.1500",
  });
  const pricey = market({
    ticker: "PRICEY-1",
    yes_bid_dollars: "0.6800",
    yes_ask_dollars: "0.7000",
    no_bid_dollars: "0.3000",
    no_ask_dollars: "0.3200",
  });
  const longshot = market({
    ticker: "LONGSHOT-1",
    yes_bid_dollars: "0.0400",
    yes_ask_dollars: "0.0500",
  });
  const ranked = rankMarkets(
    [pricey, longshot, cheap],
    [
      signal({ market_ticker: "CHEAP-1", confidence: 0.8 }),
      signal({ market_ticker: "PRICEY-1", confidence: 0.9, key: "rcp_polling_average", detail: "RCP average is 71 versus a 70 cent ask" }),
      signal({ market_ticker: "LONGSHOT-1", confidence: 0.4, key: "early_vote_share", detail: "Early vote share is 41 percent, still a lean" }),
    ],
    filters(),
  );
  assert.deepEqual(ranked.recommendations.map((row) => row.ticker), ["CHEAP-1", "PRICEY-1"]);
  const [best, second] = ranked.recommendations;
  assert.ok(best.edge_net_cents > second.edge_net_cents);
  assert.equal(best.score, best.edge_net_cents);
  assert.equal(best.confidence, 0.8);
  assert.equal(best.stake, 0.15);
  assert.equal(best.flb_band, "10–25¢");
  assert.equal(best.category_tag, "Weather");
  assert.equal(best.corr_group, "city_weather_week");
  assert.equal(best.settlement_match_score, 1);
  assert.equal(typeof best.maker_flag, "boolean");
  assert.ok(best.depth_at_limit >= 10);
  assert.equal(best.fee_entry_cents >= 1, true);
  assert.equal(best.stake_mode, "fixed_2");
  assert.equal(best.kelly_frac, 0.25);
  assert.equal(best.volume_floor_ok, false);
  assert.equal(best.edge_after_fees, best.edge_net_cents);
  assert.deepEqual(best.keys, ["nhc_cone_includes_city"]);
  assert.equal(ranked.skipped.low_confidence, 1);
});

test("a dominated yes leg is dropped when the yes mids sum through 1 plus fees", () => {
  const ranked = rankMarkets(
    [
      market({
        ticker: "YES-HI",
        event_ticker: "SPLIT-1",
        yes_bid_dollars: "0.5800",
        yes_ask_dollars: "0.6200",
        yes_ask_size_fp: "80.00",
      }),
      market({
        ticker: "YES-LO",
        event_ticker: "SPLIT-1",
        yes_bid_dollars: "0.5300",
        yes_ask_dollars: "0.5700",
        yes_ask_size_fp: "80.00",
      }),
    ],
    [
      signal({
        market_ticker: "YES-HI",
        confidence: 0.8,
        key: "city_high_model",
        detail: "Ensemble mean is 4 degrees through the high strike",
      }),
      signal({
        market_ticker: "YES-LO",
        confidence: 0.75,
        key: "city_low_model",
        detail: "Ensemble mean is 3 degrees through the low strike",
        corr_group: "city_temp_split",
      }),
    ],
    filters({ min_confidence: 0 }),
  );
  assert.deepEqual(ranked.recommendations.map((row) => row.ticker), ["YES-LO"]);
  assert.equal(ranked.recommendations[0].yes_no_replicate, "cheaper_leg");
  assert.equal(ranked.skipped.yes_no_replicate, 1);
});

test("disagreement and thin books are not recommendations", () => {
  const ranked = rankMarkets(
    [
      market({ ticker: "BOTH-1" }),
      market({
        ticker: "WIDE-1",
        yes_bid_dollars: "0.0500",
        yes_ask_dollars: "0.2000",
      }),
      market({ ticker: "THIN-1", yes_ask_size_fp: "2.00" }),
      market({ ticker: "QUIET-1", volume_24h_fp: "10.00" }),
    ],
    [
      signal({ market_ticker: "BOTH-1", side: "yes" }),
      signal({
        market_ticker: "BOTH-1",
        side: "no",
        key: "poll_closed_gap",
        detail: "Final poll gap favors no by 6 points",
      }),
      signal({ market_ticker: "WIDE-1", key: "station_obs_high", detail: "Station high already 2 degrees through the strike" }),
      signal({ market_ticker: "THIN-1", key: "model_ensemble_mean", detail: "Ensemble mean is 8 degrees above the strike" }),
      signal({ market_ticker: "QUIET-1", key: "exchange_volume_spike", detail: "Not used because the book is quiet today" }),
    ],
    filters(),
  );
  assert.equal(ranked.recommendations.length, 0);
  assert.equal(ranked.skipped.conflict, 1);
  assert.equal(ranked.skipped.wide_spread, 1);
  assert.equal(ranked.skipped.thin_book, 1);
  assert.equal(ranked.skipped.illiquid, 1);
});

test("a favorite bought for win rate and a cheap take are skipped", () => {
  const ranked = rankMarkets(
    [
      market({ ticker: "MID-1", yes_bid_dollars: "0.6800", yes_ask_dollars: "0.7000" }),
      market({ ticker: "SURE-1", yes_bid_dollars: "0.9600", yes_ask_dollars: "0.9800" }),
      market({ ticker: "LOTTO-1", yes_bid_dollars: "0.0400", yes_ask_dollars: "0.0800" }),
      market({ ticker: "BLIND-1", yes_bid_dollars: "0.4000", yes_ask_dollars: "0.4200" }),
    ],
    [
      signal({ market_ticker: "MID-1", confidence: 0.7, key: "rcp_polling_average", detail: "RCP average is 62 percent" }),
      signal({ market_ticker: "SURE-1", confidence: 0.99, key: "win_rate_favorite", detail: "This side wins almost every time at 98 cents" }),
      signal({
        market_ticker: "LOTTO-1",
        confidence: 0.12,
        category_tag: "Politics",
        corr_group: "us_election_2026",
        key: "cheap_contract_bias",
        detail: "Price is 8 cents so the payoff looks large",
      }),
      signal({
        market_ticker: "BLIND-1",
        confidence: 0.7,
        model_sources: "fee-blind backtest of mid prices",
        key: "mid_price_backtest",
        detail: "Backtest ignored fees and still looked profitable",
      }),
    ],
    filters({ min_confidence: 0 }),
  );
  assert.equal(ranked.recommendations.length, 0);
  assert.equal(ranked.skipped.thin_edge, 1);
  assert.equal(ranked.skipped.favorite_wr, 1);
  assert.equal(ranked.skipped.longshot_take, 1);
  assert.equal(ranked.skipped.fee_blind, 1);
});

test("legacy cent quotes still produce a stake and a payout", () => {
  const ranked = rankMarkets(
    [market({
      yes_bid_dollars: undefined,
      yes_ask_dollars: undefined,
      yes_bid: 14,
      yes_ask: 15,
    })],
    [signal()],
    filters(),
  );
  assert.equal(ranked.recommendations[0].stake, 0.15);
  assert.equal(ranked.recommendations[0].payout, 0.85);
});

test("agreeing indicators keep every key and use the stronger confidence", () => {
  const ranked = rankMarkets(
    [market()],
    [
      signal({ confidence: 0.66, key: "rcp_polling_average", detail: "RCP average sits at 70 percent" }),
      signal({ confidence: 0.81, key: "early_vote_share", detail: "Early vote share is 64 percent of ballots" }),
    ],
    filters(),
  );
  assert.equal(ranked.recommendations.length, 1);
  assert.equal(ranked.recommendations[0].confidence, 0.81);
  assert.deepEqual(ranked.recommendations[0].keys, ["early_vote_share", "rcp_polling_average"]);
  assert.equal(ranked.recommendations[0].indicators.length, 2);
});

test("query plan stays narrow and capped", () => {
  const one = planMarketQueries(validateSignals([
    signal({ market_ticker: "B" }),
    signal({ market_ticker: "A", key: "rcp_polling_average", detail: "RCP average is 60 percent yes" }),
  ]));
  assert.equal(one.length, 1);
  assert.equal(one[0].tickers, "A,B");
  assert.equal(one[0].status, "open");

  const many = [];
  for (let i = 0; i < 60; i += 1) {
    many.push({ market_ticker: `MKT-${String(i).padStart(2, "0")}` });
  }
  const chunked = planMarketQueries(many);
  assert.equal(chunked.length, 2);
  assert.equal(chunked[0].tickers.split(",").length, 50);
  assert.equal(chunked[1].tickers.split(",").length, 10);

  const series = [];
  for (let i = 0; i < 9; i += 1) {
    series.push(signal({
      market_ticker: "",
      series_ticker: `SERIES${i}`,
      key: "model_ensemble_mean",
      detail: `Ensemble mean for series ${i} is high`,
    }));
  }
  assert.throws(() => planMarketQueries(validateSignals(series)), /max 8/);
});
