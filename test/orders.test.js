import assert from "node:assert/strict";
import test from "node:test";
import { buildCreateOrderBody, cancelOrder, placeOrder } from "../src/orders.js";

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
