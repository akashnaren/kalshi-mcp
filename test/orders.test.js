import assert from "node:assert/strict";
import test from "node:test";
import { createLedger, createLock } from "../src/caps.js";
import { research } from "./helpers.js";
import { amendOrder, buildCreateOrderBody, cancelOrder, decreaseOrder, exitPosition, placeOrder } from "../src/orders.js";

test("order body is a V2 limit with a panic cancel", () => {
  const body = buildCreateOrderBody({
    ticker: "KXTEST-26-T1",
    side: "bid",
    count: 2,
    price: 0.15,
    timeInForce: "good_till_canceled",
    clientOrderId: "11111111-1111-1111-1111-111111111111",
  });
  assert.equal(body.side, "bid");
  assert.equal(body.count, "2.00");
  assert.equal(body.price, "0.1500");
  assert.equal(body.cancel_order_on_pause, true);
  assert.equal(body.self_trade_prevention_type, "taker_at_cross");
});

test("place and cancel do not touch the client until both gates pass", async () => {
  const calls = [];
  const client = {
    async createOrder(body) {
      calls.push(["create", body]);
      return { order_id: "abc12345" };
    },
    async cancelOrder(orderId, query) {
      calls.push(["cancel", orderId, query]);
      return { order_id: orderId, reduced_by: "1.00" };
    },
  };
  const safe = { KALSHI_SAFE_MODE: "1" };
  const open = { KALSHI_SAFE_MODE: "0" };

  await assert.rejects(() => placeOrder({
    client, env: safe, confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 1, price: 0.2,
  }), /SAFE_MODE/);
  await assert.rejects(() => placeOrder({
    client, env: open, confirm: false, ticker: "KXTEST-26-T1", side: "bid", count: 1, price: 0.2,
  }), /confirm:true/);
  await assert.rejects(() => cancelOrder({
    client, env: safe, confirm: true, orderId: "abc12345", marketTicker: "KXTEST-26-T1",
  }), /SAFE_MODE/);
  assert.equal(calls.length, 0);

  const placed = await placeOrder({
    client, env: open, confirm: true, ticker: "KXTEST-26-T1", side: "ask", count: 1.5, price: 0.8, research: research(),
  });
  assert.equal(placed.placed, true);
  assert.equal(calls[0][1].side, "ask");
  assert.equal(calls[0][1].price, "0.8000");

  await cancelOrder({
    client, env: open, confirm: true, orderId: "abc12345", marketTicker: "KXTEST-26-T1",
  });
  assert.equal(calls[1][0], "cancel");
  assert.equal(calls[1][2].exchange_index, -1);
});

test("place refuses size over the caps and still records what it sent", async () => {
  const calls = [];
  const ledger = createLedger();
  const client = {
    async createOrder(body) {
      calls.push(body);
      return { order_id: "abc12345" };
    },
  };
  const env = { KALSHI_SAFE_MODE: "0" };
  await assert.rejects(() => placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 20, price: 0.5, research: research(),
  }), /profitable/);
  assert.equal(calls.length, 0);

  await placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 10, price: 0.2,
    research: research({ corr_group: "city_weather_week" }),
  });
  await placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T2", side: "bid", count: 10, price: 0.2,
    research: research({ corr_group: "fed_path" }),
  });
  await assert.rejects(() => placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T3", side: "bid", count: 50, price: 0.2,
    research: research({ corr_group: "nfl_week_n" }),
  }), /daily/);
  assert.equal(calls.length, 2);
  assert.equal(ledger.snapshot().daily, 4);
});

test("parallel orders cannot slip past the daily cap", async () => {
  const calls = [];
  const ledger = createLedger();
  const lock = createLock();
  const client = {
    async createOrder(body) {
      calls.push(body.ticker);
      await new Promise((resolve) => setTimeout(resolve, 15));
      return { order_id: "abc12345" };
    },
  };
  const env = { KALSHI_SAFE_MODE: "0" };
  const results = await Promise.allSettled([...Array(6).keys()].map((i) => placeOrder({
    client,
    env,
    ledger,
    lock,
    confirm: true,
    ticker: `KXPAR-${i}`,
    side: "bid",
    count: 10,
    price: 0.2,
    research: research({ corr_group: `driver_${i}` }),
  })));
  const placed = results.filter((result) => result.status === "fulfilled");
  assert.equal(placed.length, 5);
  assert.equal(calls.length, 5);
  assert.equal(ledger.snapshot().daily, 10);
});

test("exit closes a long even when the daily cap is full", async () => {
  const calls = [];
  const ledger = createLedger();
  ledger.record({ ticker: "OTHER", notional: 10, client_order_id: "full", at: Date.now() });
  const client = {
    async getPositions() {
      return { market_positions: [{ ticker: "KXTEST-26-T1", position_fp: "4.00", market_exposure_dollars: "1.00" }] };
    },
    async createOrder(body) {
      calls.push(body);
      return { order_id: "abc12345" };
    },
  };
  const exited = await exitPosition({
    client,
    env: { KALSHI_SAFE_MODE: "0" },
    confirm: true,
    ticker: "KXTEST-26-T1",
    price: 0.4,
    falsifierHit: true,
  });
  assert.equal(exited.exited, true);
  assert.equal(calls[0].side, "ask");
  assert.equal(calls[0].count, "4.00");
  assert.equal(calls[0].time_in_force, "immediate_or_cancel");
  assert.equal(ledger.snapshot().daily, 10);
});

test("amend cannot grow past the small default until the sleeve has won, decrease can shrink", async () => {
  const calls = [];
  const resting = {
    order_id: "abc12345",
    ticker: "KXTEST-26-T1",
    status: "resting",
    action: "buy",
    side: "yes",
    remaining_count_fp: "10.00",
    yes_price_dollars: "0.1000",
    client_order_id: "cid-1",
    created_time: new Date().toISOString(),
  };
  const client = {
    async getOrders() {
      return { orders: [resting] };
    },
    async getPositions() {
      return { market_positions: [] };
    },
    async getFills() {
      return { fills: [] };
    },
    async amendOrder(orderId, body) {
      calls.push(["amend", orderId, body]);
      return { order_id: orderId };
    },
    async decreaseOrder(orderId, body) {
      calls.push(["decrease", orderId, body]);
      return { order_id: orderId, remaining_count: "8.00" };
    },
  };
  const env = { KALSHI_SAFE_MODE: "0" };
  await assert.rejects(() => amendOrder({
    client, env, confirm: true, orderId: "abc12345", ticker: "KXTEST-26-T1", side: "bid", price: 0.1, count: 40,
  }), /profitable/);
  assert.equal(calls.length, 0);

  const amended = await amendOrder({
    client, env, confirm: true, orderId: "abc12345", ticker: "KXTEST-26-T1", side: "bid", price: 0.1, count: 8,
  });
  assert.equal(amended.amended, true);
  assert.equal(amended.delta < 0, true);
  assert.equal(calls[0][0], "amend");

  await decreaseOrder({
    client, env, confirm: true, orderId: "abc12345", marketTicker: "KXTEST-26-T1", reduceBy: 2,
  });
  assert.equal(calls[1][0], "decrease");
  assert.equal(calls[1][2].reduce_by, "2.00");
});

test("a profitable sleeve can size up, and nothing clears the hard max", async () => {
  const calls = [];
  const resting = {
    order_id: "abc12345",
    ticker: "KXTEST-26-T1",
    status: "resting",
    action: "buy",
    side: "yes",
    remaining_count_fp: "10.00",
    yes_price_dollars: "0.1000",
    client_order_id: "cid-1",
    created_time: new Date().toISOString(),
  };
  const client = {
    async getPositions() {
      return {
        market_positions: [{
          ticker: "OLD",
          position_fp: "0",
          realized_pnl_dollars: "4.00",
          fees_paid_dollars: "0.20",
          market_exposure_dollars: "0",
        }],
      };
    },
    async getFills() {
      return {
        fills: [0, 1, 2].map((i) => ({
          action: "sell",
          ticker: "OLD",
          count_fp: "1.00",
          yes_price_dollars: "0.4000",
          created_time: "2020-01-02T00:00:00Z",
          trade_id: `old-${i}`,
        })),
      };
    },
    async getOrders() {
      return { orders: [resting] };
    },
    async createOrder(body) {
      calls.push(body);
      return { order_id: "def67890" };
    },
    async amendOrder(orderId, body) {
      calls.push(["amend", orderId, body]);
      return { order_id: orderId };
    },
  };
  const env = {
    KALSHI_SAFE_MODE: "0",
    KALSHI_SLEEVE_DOLLARS: "200",
    KALSHI_MAX_DAILY_NOTIONAL: "20",
    KALSHI_MAX_DOLLARS_PER_TRADE: "100",
  };
  await assert.rejects(() => placeOrder({
    client, env, confirm: true, ticker: "KXNEW-1", side: "bid", count: 20, price: 0.8,
    research: research({ p_model: 0.95, corr_group: "fed_path" }),
  }), /hard max/);
  await assert.rejects(() => placeOrder({
    client, env, confirm: true, ticker: "KXNEW-1", side: "bid", count: 100, price: 0.15,
    research: research({ corr_group: "city_weather_week" }),
  }), /fixed_2/);
  const placed = await placeOrder({
    client, env, confirm: true, ticker: "KXNEW-1", side: "bid", count: 10, price: 0.6,
    research: research({ p_model: 0.85, corr_group: "fed_path" }),
  });
  assert.equal(placed.notional, 6);
  assert.equal(placed.stake_mode, "modest");
  assert.equal(calls[0].count, "10.00");

  const grown = await amendOrder({
    client, env, confirm: true, orderId: "abc12345", ticker: "KXTEST-26-T1", side: "bid", price: 0.1, count: 20,
  });
  assert.equal(grown.amended, true);
  assert.equal(grown.delta, 1);
});
