import crypto from "node:crypto";
import { assertCanMutate } from "./policy.js";

const ORDER_ID_RE = /^[A-Za-z0-9-]{8,80}$/;

export function buildCreateOrderBody({ ticker, side, count, price, timeInForce, clientOrderId }) {
  return {
    ticker,
    client_order_id: clientOrderId || crypto.randomUUID(),
    side,
    count: Number(count).toFixed(2),
    price: Number(price).toFixed(4),
    time_in_force: timeInForce,
    self_trade_prevention_type: "taker_at_cross",
    post_only: false,
    cancel_order_on_pause: true,
  };
}

export async function placeOrder({ client, env, confirm, ticker, side, count, price, timeInForce }) {
  assertCanMutate(confirm, env);
  const body = buildCreateOrderBody({
    ticker,
    side,
    count,
    price,
    timeInForce: timeInForce || "good_till_canceled",
  });
  const order = await client.createOrder(body);
  return {
    placed: true,
    request: body,
    order,
    note: "Sent only because KALSHI_SAFE_MODE was off and confirm was true.",
  };
}

export async function cancelOrder({ client, env, confirm, orderId, marketTicker }) {
  assertCanMutate(confirm, env);
  if (!ORDER_ID_RE.test(orderId)) {
    throw new Error("order_id must be the Kalshi order id.");
  }
  const result = await client.cancelOrder(orderId, {
    market_ticker: marketTicker,
    exchange_index: -1,
  });
  return {
    cancelled: true,
    result,
    note: "Cancelled only because KALSHI_SAFE_MODE was off and confirm was true.",
  };
}
