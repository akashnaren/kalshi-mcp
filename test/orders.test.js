import assert from "node:assert/strict";
import test from "node:test";
import { createLedger, createLock } from "../src/caps.js";
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
    client, env: open, confirm: true, ticker: "KXTEST-26-T1", side: "ask", count: 1.5, price: 0.8,
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
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 20, price: 0.5,
  }), /per idea/);
  assert.equal(calls.length, 0);

  await placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 10, price: 0.2,
  });
  await placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T2", side: "bid", count: 10, price: 0.2,
  });
  await assert.rejects(() => placeOrder({
    client, env, ledger, confirm: true, ticker: "KXTEST-26-T3", side: "bid", count: 40, price: 0.2,
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
  const results = await Promise.allSettled([0, 1, 2, 3, 4, 5].map((i) => placeOrder({
    client,
    env,
    ledger,
    lock,
    confirm: true,
    ticker: `KXPAR-${i}`,
    side: "bid",
    count: 10,
    price: 0.2,
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
  });
  assert.equal(exited.exited, true);
  assert.equal(calls[0].side, "ask");
  assert.equal(calls[0].count, "4.00");
  assert.equal(calls[0].time_in_force, "immediate_or_cancel");
  assert.equal(ledger.snapshot().daily, 10);
});

test("amend cannot grow an idea past $2, decrease can shrink it", async () => {
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
    client, env, confirm: true, orderId: "abc12345", ticker: "KXTEST-26-T1", side: "bid", price: 0.1, count: 30,
  }), /per idea/);
  assert.equal(calls.length, 0);

  const amended = await amendOrder({
    client, env, confirm: true, orderId: "abc12345", ticker: "KXTEST-26-T1", side: "bid", price: 0.1, count: 20,
  });
  assert.equal(amended.amended, true);
  assert.equal(calls[0][0], "amend");

  await decreaseOrder({
    client, env, confirm: true, orderId: "abc12345", marketTicker: "KXTEST-26-T1", reduceBy: 2,
  });
  assert.equal(calls[1][0], "decrease");
  assert.equal(calls[1][2].reduce_by, "2.00");
});
