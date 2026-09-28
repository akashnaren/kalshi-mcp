import assert from "node:assert/strict";
import test from "node:test";
import {
  CAP_DEFAULTS,
  HARD_TRADE_CEILING,
  assessWinHistory,
  checkBuyCaps,
  createLedger,
  loadCaps,
  openingNotional,
  reviewPositions,
  sizeIdeas,
  summarizeBook,
} from "../src/caps.js";

const caps = () => loadCaps({});

test("defaults match the starter sleeve and the ceiling cannot be raised", () => {
  const loaded = caps();
  assert.equal(loaded.sleeve_dollars, 71);
  assert.equal(loaded.default_dollars_per_trade, CAP_DEFAULTS.default_dollars_per_trade);
  assert.equal(loaded.max_dollars_per_trade, HARD_TRADE_CEILING);
  assert.equal(loaded.max_sleeve_fraction, 0.15);
  assert.equal(loaded.max_group_fraction, 0.3);
  assert.equal(loaded.max_daily_notional, 10);
  assert.equal(loadCaps({ KALSHI_MAX_GROUP_FRACTION: "0.5" }).max_group_fraction, 0.3);
  assert.equal(loadCaps({ KALSHI_MAX_DOLLARS_PER_TRADE: "100" }).max_dollars_per_trade, 15);
  assert.equal(loadCaps({ KALSHI_MAX_DOLLARS_PER_TRADE: "nope" }).max_dollars_per_trade, 15);
  assert.equal(loadCaps({ KALSHI_MAX_DOLLARS_PER_TRADE: "8" }).max_dollars_per_trade, 8);
  assert.equal(loadCaps({
    KALSHI_DEFAULT_DOLLARS_PER_TRADE: "3",
    KALSHI_MAX_DOLLARS_PER_TRADE: "2",
  }).default_dollars_per_trade, 2);
});

test("caps block a raise without a winning history, a hard max, a concentrated market, a full day, and an all-in", () => {
  const base = caps();
  const won = { allows_scale: true };
  assert.equal(checkBuyCaps({ notional: 2, marketExposure: 0, dailyNotional: 0, caps: base }).ok, true);
  assert.match(
    checkBuyCaps({ notional: 2.01, marketExposure: 0, dailyNotional: 0, caps: base }).reasons.join(" "),
    /profitable/,
  );
  assert.match(checkBuyCaps({ notional: 1, marketExposure: 10, dailyNotional: 0, caps: base }).reasons.join(" "), /15%/);
  assert.match(checkBuyCaps({ notional: 1, marketExposure: 0, dailyNotional: 9.5, caps: base }).reasons.join(" "), /daily/);

  const roomy = { ...base, sleeve_dollars: 200, max_daily_notional: 20 };
  assert.match(
    checkBuyCaps({ notional: 16, marketExposure: 0, dailyNotional: 0, caps: roomy, history: won }).reasons.join(" "),
    /hard max \$15/,
  );
  assert.equal(
    checkBuyCaps({ notional: 15, marketExposure: 0, dailyNotional: 0, caps: { ...roomy, max_dollars_per_trade: 100 }, history: won }).ok,
    true,
  );

  const wideOpen = {
    ...base,
    sleeve_dollars: 10,
    max_sleeve_fraction: 1,
    max_daily_notional: 20,
  };
  assert.match(
    checkBuyCaps({ notional: 10, marketExposure: 0, dailyNotional: 0, caps: wideOpen, history: won }).reasons.join(" "),
    /all-in/,
  );
});

const strongIdea = {
  ticker: "STRONG",
  stake: 0.25,
  confidence: 1,
  corr_group: "city_weather_week",
  depth_at_limit: 200,
};

test("quarter Kelly stays near $2 until history is profitable, then half Kelly can reach $15", () => {
  const ideas = [];
  for (let i = 0; i < 6; i += 1) {
    ideas.push({ ticker: `M${i}`, stake: 0.5, confidence: 0.8, corr_group: `driver_${i}`, depth_at_limit: 100 });
  }
  const sized = sizeIdeas(ideas, { caps: caps(), marketExposure: {}, dailyNotional: 0 });
  const dollars = sized.recommendations.map((row) => row.suggested_dollars);
  assert.deepEqual(dollars, [2, 2, 2, 2, 2]);
  assert.equal(sized.unsized.some((row) => row.ticker === "M5" && row.blocked_by === "daily_cap"), true);

  const wide = loadCaps({ KALSHI_SLEEVE_DOLLARS: "200", KALSHI_MAX_DAILY_NOTIONAL: "20" });
  const cold = sizeIdeas([strongIdea], { caps: wide, history: { allows_scale: false } });
  assert.equal(cold.recommendations[0].suggested_dollars, 2);
  const hot = sizeIdeas([strongIdea], { caps: wide, history: { allows_scale: true } });
  assert.equal(hot.recommendations[0].suggested_dollars, 15);

  const thin = sizeIdeas(
    [{ ticker: "THIN", stake: 0.4, confidence: 0.42, corr_group: "fed_path", depth_at_limit: 50 }],
    { caps: caps(), dailyNotional: 0 },
  );
  assert.ok(thin.recommendations[0].suggested_dollars < 2);
  assert.ok(thin.recommendations[0].suggested_dollars > 0);

  const crowded = sizeIdeas(
    [{ ticker: "HELD", stake: 0.25, confidence: 0.8, corr_group: "fed_path", depth_at_limit: 40 }],
    { caps: caps(), marketExposure: { HELD: 10.65 }, dailyNotional: 0 },
  );
  assert.equal(crowded.recommendations.length, 0);
  assert.equal(crowded.unsized[0].blocked_by, "market_cap");
});

test("one corr_group shares a 30 percent cap even when the tickers differ", () => {
  const wideDay = loadCaps({ KALSHI_MAX_DAILY_NOTIONAL: "40" });
  const ideas = [];
  for (let i = 0; i < 12; i += 1) {
    ideas.push({ ticker: `G${i}`, stake: 0.5, confidence: 0.9, corr_group: "us_election_2026", depth_at_limit: 80 });
  }
  const sized = sizeIdeas(ideas, { caps: wideDay, dailyNotional: 0 });
  const spent = sized.recommendations.reduce((sum, row) => sum + row.suggested_dollars, 0);
  assert.ok(spent <= 21.3);
  assert.equal(sized.unsized.some((row) => row.blocked_by === "corr_group"), true);
});

test("bankroll stays at 40 percent, one event stays near 20 percent, and the book stops at 15 names", () => {
  const wideDay = loadCaps({ KALSHI_MAX_DAILY_NOTIONAL: "40" });
  const locked = sizeIdeas(
    [{ ticker: "NEW", stake: 0.5, confidence: 0.9, corr_group: "fed_path", depth_at_limit: 80 }],
    { caps: wideDay, marketExposure: { LOCK: 28.4 }, dailyNotional: 0 },
  );
  assert.equal(locked.unsized[0].blocked_by, "bankroll_util");

  const eventIdeas = [];
  for (let i = 0; i < 9; i += 1) {
    eventIdeas.push({
      ticker: `E${i}`,
      event_ticker: "SAME-EVENT",
      stake: 0.5,
      confidence: 0.9,
      corr_group: `driver_${i}`,
      depth_at_limit: 80,
    });
  }
  const eventSized = sizeIdeas(eventIdeas, { caps: wideDay, dailyNotional: 0 });
  const eventSpent = eventSized.recommendations.reduce((sum, row) => sum + row.suggested_dollars, 0);
  assert.ok(eventSpent <= 14.2);
  assert.equal(eventSized.unsized.some((row) => row.blocked_by === "max_per_event"), true);

  const open = {};
  for (let i = 0; i < 15; i += 1) open[`OPEN${i}`] = 1;
  const full = sizeIdeas(
    [{ ticker: "SIXTEENTH", stake: 0.5, confidence: 0.9, corr_group: "fresh_driver", depth_at_limit: 80 }],
    { caps: wideDay, marketExposure: open, dailyNotional: 0 },
  );
  assert.equal(full.unsized[0].blocked_by, "open_positions");
});

test("win history scales only after three closes and a positive net", () => {
  const sells = [0, 1, 2].map((i) => ({
    action: "sell",
    ticker: "OLD",
    count_fp: "1.00",
    yes_price_dollars: "0.4000",
    created_time: "2020-01-01T00:00:00Z",
    trade_id: `t${i}`,
  }));
  const winning = assessWinHistory({
    positions: { market_positions: [{ ticker: "OLD", realized_pnl_dollars: "2.50", fees_paid_dollars: "0.10" }] },
    fills: { fills: sells },
  });
  assert.equal(winning.closed_trades, 3);
  assert.equal(winning.net_realized, 2.4);
  assert.equal(winning.allows_scale, true);

  const cents = assessWinHistory({
    positions: { market_positions: [{ ticker: "OLD", realized_pnl: 250, fees_paid: 10 }] },
    fills: { fills: sells },
  });
  assert.equal(cents.allows_scale, true);

  const short = assessWinHistory({
    positions: { market_positions: [{ ticker: "OLD", realized_pnl_dollars: "2.50" }] },
    fills: { fills: sells.slice(0, 2) },
  });
  assert.equal(short.closed_trades, 2);
  assert.equal(short.allows_scale, false);

  const losing = assessWinHistory({
    positions: { market_positions: [{ ticker: "OLD", realized_pnl_dollars: "-1.00", fees_paid_dollars: "0.10" }] },
    fills: { fills: sells },
  });
  assert.equal(losing.allows_scale, false);
  assert.equal(assessWinHistory({}).allows_scale, false);
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
  assert.deepEqual(reviews.map((row) => row.action), ["hold", "cut", "hold"]);
  const falsified = reviewPositions([
    { ticker: "NEWS", cost: 1, mark: 1.2, falsifier_hit: true },
    { ticker: "FLIP", cost: 1, mark: 1.1, edge_net_cents: -4, fee_exit_cents: 2 },
  ], rules);
  assert.deepEqual(falsified.map((row) => row.action), ["cut", "cut"]);
  const noise = reviewPositions([
    { ticker: "NOISE", cost: 1, mark: 0.99, edge_net_cents: -1 },
    { ticker: "GONE", cost: 1, mark: 1.1, edge_net_cents: -2 },
  ], rules);
  assert.deepEqual(noise.map((row) => row.action), ["hold", "cut"]);
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

test("old sells count as win history and not as today's notional", () => {
  const book = summarizeBook({
    positions: {
      market_positions: [{ ticker: "OLD", position_fp: "0", realized_pnl_dollars: "3.00", fees_paid_dollars: "0.25" }],
    },
    fills: {
      fills: [0, 1, 2].map((i) => ({
        action: "sell",
        ticker: "OLD",
        count_fp: "2.00",
        yes_price_dollars: "0.5000",
        created_time: "2020-01-01T00:00:00Z",
        trade_id: `old-${i}`,
      })),
    },
    orders: { orders: [] },
  });
  assert.equal(book.daily, 0);
  assert.equal(book.history.allows_scale, true);
  assert.equal(book.history.net_realized, 2.75);
});
