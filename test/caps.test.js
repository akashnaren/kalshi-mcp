import assert from "node:assert/strict";
import test from "node:test";
import {
  CAP_DEFAULTS,
  checkBuyCaps,
  createLedger,
  loadCaps,
  openingNotional,
  reviewPositions,
  sizeIdeas,
  summarizeBook,
} from "../src/caps.js";

const caps = () => loadCaps({});

test("defaults match the starter sleeve", () => {
  const loaded = caps();
  assert.equal(loaded.sleeve_dollars, 71);
  assert.equal(loaded.max_dollars_per_idea, CAP_DEFAULTS.max_dollars_per_idea);
  assert.equal(loaded.max_sleeve_fraction, 0.15);
  assert.equal(loaded.max_daily_notional, 10);
  assert.equal(loaded.likely_size_fraction, 0.5);
  assert.equal(loadCaps({ KALSHI_MAX_DOLLARS_PER_IDEA: "nope" }).max_dollars_per_idea, 2);
});

test("caps block an oversized idea, a concentrated market, a full day, and an all-in", () => {
  const base = caps();
  assert.equal(checkBuyCaps({ notional: 2, marketExposure: 0, dailyNotional: 0, caps: base }).ok, true);
  assert.match(checkBuyCaps({ notional: 2.01, marketExposure: 0, dailyNotional: 0, caps: base }).reasons.join(" "), /per idea/);
  assert.match(checkBuyCaps({ notional: 2, marketExposure: 9, dailyNotional: 0, caps: base }).reasons.join(" "), /15%/);
  assert.match(checkBuyCaps({ notional: 2, marketExposure: 0, dailyNotional: 9, caps: base }).reasons.join(" "), /daily/);

  const wideOpen = {
    ...base,
    max_dollars_per_idea: 71,
    max_sleeve_fraction: 1,
    max_daily_notional: 71,
  };
  assert.match(
    checkBuyCaps({ notional: 71, marketExposure: 0, dailyNotional: 0, caps: wideOpen }).reasons.join(" "),
    /all-in/,
  );
});

test("sizing spends the daily budget once and keeps a likely idea modest", () => {
  const ideas = [];
  for (let i = 0; i < 6; i += 1) {
    ideas.push({ ticker: `M${i}`, lane: "asymmetric", stake: 0.5, score: 6 - i });
  }
  ideas.push({ ticker: "FAV", lane: "likely", stake: 0.8, score: 0.9 });
  const sized = sizeIdeas(ideas, { caps: caps(), marketExposure: {}, dailyNotional: 0 });
  const dollars = sized.recommendations.map((row) => row.suggested_dollars);
  assert.deepEqual(dollars.slice(0, 5), [2, 2, 2, 2, 2]);
  assert.equal(sized.unsized.some((row) => row.ticker === "M5" && row.blocked_by === "daily_cap"), true);
  const favorite = sized.recommendations.find((row) => row.ticker === "FAV");
  assert.equal(favorite, undefined);

  const onlyFavorite = sizeIdeas(
    [{ ticker: "FAV", lane: "likely", stake: 0.8, score: 0.9 }],
    { caps: caps(), dailyNotional: 0 },
  );
  assert.equal(onlyFavorite.recommendations[0].suggested_dollars, 1);

  const crowded = sizeIdeas(
    [{ ticker: "HELD", lane: "asymmetric", stake: 0.25, score: 3 }],
    { caps: caps(), marketExposure: { HELD: 10.65 }, dailyNotional: 0 },
  );
  assert.equal(crowded.recommendations.length, 0);
  assert.equal(crowded.unsized[0].blocked_by, "market_cap");
});

test("closing a long does not count as new risk", () => {
  assert.equal(openingNotional({ side: "ask", count: 5, price: 0.7, positionContracts: 5 }), 0);
  assert.equal(openingNotional({ side: "bid", count: 4, price: 0.25, positionContracts: 0 }), 1);
  assert.equal(openingNotional({ side: "ask", count: 2, price: 0.8, positionContracts: 0 }), 0.4);
});

test("review marks take profit, cut, and hold", () => {
  const rules = caps();
  const reviews = reviewPositions([
    { ticker: "WIN", cost: 1, mark: 1.5 },
    { ticker: "LOSE", cost: 1, mark: 0.5 },
    { ticker: "FLAT", cost: 1, mark: 1.1 },
  ], rules);
  assert.deepEqual(reviews.map((row) => row.action), ["take_profit", "cut", "hold"]);
});

test("the ledger does not double count an order the book already has", () => {
  const ledger = createLedger();
  ledger.record({ ticker: "KX", notional: 2, client_order_id: "cid-1", at: Date.now() });
  const book = summarizeBook({
    positions: { market_positions: [] },
    fills: { fills: [] },
    orders: {
      orders: [{
        ticker: "KX",
        status: "resting",
        action: "buy",
        side: "yes",
        remaining_count_fp: "10.00",
        yes_price_dollars: "0.2000",
        client_order_id: "cid-1",
        created_time: new Date().toISOString(),
      }],
    },
    ledger,
  });
  assert.equal(book.daily, 2);
  assert.equal(book.byTicker.KX, 2);
});
