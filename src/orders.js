import crypto from "node:crypto";
import {
  checkBuyCaps,
  deployedDollars,
  loadCaps,
  loadRiskBook,
  openPositionCount,
  openingNotional,
  orderNotional,
  signedContracts,
  tradeSizeReasons,
} from "./caps.js";
import { assertEarlyExit, evaluateEntry, fractionalKellyDollars, isFeeBlind, stakeCapDollars } from "./edge.js";
import { assertCanMutate, PolicyError, round4 } from "./policy.js";

const ORDER_ID_RE = /^[A-Za-z0-9-]{8,80}$/;

function rowsOf(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

function assertOrderId(orderId) {
  if (!ORDER_ID_RE.test(orderId || "")) {
    throw new PolicyError("BAD_ORDER", "order_id must be the Kalshi order id.");
  }
}

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

async function bookOrRefuse(client, ledger) {
  try {
    return await loadRiskBook(client, ledger);
  } catch (err) {
    throw new PolicyError("CAP", `Refusing to add risk because the book could not be loaded: ${err.message}`);
  }
}

function enforceCaps({
  notional,
  ticker,
  book,
  caps,
  history,
  checkSize = true,
  kellyDollars,
  corrGroup,
  groupExposure = 0,
  stakeMode,
  eventTicker,
}) {
  const verdict = checkBuyCaps({
    notional,
    marketExposure: book.byTicker[ticker] || 0,
    dailyNotional: book.daily,
    caps,
    history: history ?? book.history,
    checkSize,
    kellyDollars,
    corrGroup,
    groupExposure,
    stakeMode,
    bankrollDeployed: deployedDollars(book.byTicker),
    openPositions: openPositionCount(book.byTicker),
    eventTicker,
    eventExposure: eventTicker ? (book.byEvent?.[eventTicker] || 0) : 0,
  });
  if (!verdict.ok) {
    throw new PolicyError("CAP", verdict.reasons.join("; "));
  }
  return verdict;
}

function entryPrice(side, price) {
  return side === "ask" ? round4(1 - Number(price)) : Number(price);
}

function assertOpeningEdge({ side, price, research, timeInForce }) {
  const complete = research && research.p_model != null && research.category_tag && research.corr_group
    && research.model_sources && research.settlement_match_score != null;
  if (!complete) {
    throw new PolicyError(
      "BAD_EDGE",
      "Opening risk needs p_model, category_tag, corr_group, model_sources, settlement_match_score, and horizon_days.",
    );
  }
  if (isFeeBlind(`${research.model_sources || ""} ${research.detail || ""}`)) {
    throw new PolicyError("BAD_EDGE", "Fee-blind backtests are not a reason to trade.");
  }
  const decision = evaluateEntry({
    pModel: research.p_model,
    ask: entryPrice(side, price),
    bid: research.bid,
    horizonDays: research.horizon_days ?? 0,
    holdToSettle: research.hold_to_settle !== false,
    depth: research.depth_at_limit ?? 0,
    settlementMatch: Number(research.settlement_match_score),
  });
  if (!decision.ok) {
    throw new PolicyError("BAD_EDGE", `Refusing open: ${decision.reason}. Gate is edge_net after fee_dome, spread, and depth.`);
  }
  const tif = timeInForce || "good_till_canceled";
  if (decision.maker_flag && (tif === "immediate_or_cancel" || tif === "fill_or_kill")) {
    throw new PolicyError("BAD_EDGE", "prefer maker when edge is small; rest a good_till_canceled limit");
  }
  return decision;
}

export async function placeOrder({
  client,
  env,
  ledger,
  lock,
  confirm,
  ticker,
  side,
  count,
  price,
  timeInForce,
  research,
}) {
  assertCanMutate(confirm, env);
  const run = async () => {
    const caps = loadCaps(env);
    const book = await bookOrRefuse(client, ledger);
    const notional = openingNotional({
      side,
      count,
      price,
      positionContracts: book.contractsByTicker[ticker] || 0,
    });
    let decision = null;
    let kellyDollars;
    if (notional > 0) {
      decision = assertOpeningEdge({ side, price, research, timeInForce });
      const groupName = research.corr_group;
      const groupExposure = book.byGroup?.[groupName] || 0;
      kellyDollars = Math.min(
        fractionalKellyDollars({
          p: research.p_model,
          price: entryPrice(side, price),
          bankroll: caps.sleeve_dollars,
          groupAlreadyOpen: groupExposure > 0,
          drawdown: Number(research.drawdown_from_peak) || 0,
        }),
        stakeCapDollars(decision.stake_mode, book.history),
      );
      if (drawdownBlocks(research)) {
        throw new PolicyError("CAP", "drawdown from peak is at least 40 percent; new risk is halted");
      }
    }
    const verdict = notional > 0
      ? enforceCaps({
        notional,
        ticker,
        book,
        caps,
        history: book.history,
        kellyDollars,
        corrGroup: research?.corr_group,
        groupExposure: book.byGroup?.[research?.corr_group] || 0,
        stakeMode: decision.stake_mode,
        eventTicker: research?.event_ticker,
      })
      : { ok: true, notional: 0 };
    const body = buildCreateOrderBody({
      ticker,
      side,
      count,
      price,
      timeInForce: timeInForce || "good_till_canceled",
    });
    if (notional > 0 && ledger) {
      ledger.record({
        ticker,
        notional,
        client_order_id: body.client_order_id,
        corr_group: research?.corr_group,
      });
    }
    try {
      const order = await client.createOrder(body);
      return {
        placed: true,
        notional,
        caps: verdict,
        request: body,
        order,
        edge_net_cents: decision?.edge_net_cents ?? null,
        maker_flag: decision?.maker_flag ?? false,
        side_exec: decision?.side_exec ?? null,
        stake_mode: decision?.stake_mode ?? null,
        note: "Sent because KALSHI_SAFE_MODE was off, confirm was true, edge_net cleared the fee dome, and the caps allowed it.",
      };
    } catch (err) {
      if (notional > 0 && ledger) ledger.release(body.client_order_id, notional);
      throw err;
    }
  };
  return lock ? lock(run) : run();
}

function drawdownBlocks(research) {
  return Number(research?.drawdown_from_peak) >= 0.4;
}

export async function exitPosition({ client, env, confirm, ticker, count, price, falsifierHit, edgeNetCents }) {
  assertCanMutate(confirm, env);
  const gate = assertEarlyExit({ falsifierHit, edgeNetCents, exitPrice: price });
  if (!gate.ok) throw new PolicyError("HOLD", gate.reason);
  const payload = await client.getPositions({ ticker });
  const row = rowsOf(payload, "market_positions", "positions").find((item) => item.ticker === ticker);
  const pos = signedContracts(row);
  if (!pos) throw new PolicyError("NO_POSITION", `No open position in ${ticker} to exit.`);
  const open = Math.abs(pos);
  const closing = count == null ? open : Number(count);
  if (!(closing > 0) || closing > open) {
    throw new PolicyError("BAD_ORDER", `exit count must be between 0 and the open ${open} contracts.`);
  }
  const side = pos > 0 ? "ask" : "bid";
  const body = buildCreateOrderBody({
    ticker,
    side,
    count: closing,
    price,
    timeInForce: "immediate_or_cancel",
  });
  const order = await client.createOrder(body);
  return {
    exited: true,
    side,
    count: closing,
    request: body,
    order,
    note: "Exit only. This does not add risk and does not withdraw funds.",
  };
}

async function findOrder(client, orderId, ticker) {
  if (!client.getOrders) return null;
  try {
    const payload = await client.getOrders({ ticker, status: "resting", limit: 200 });
    return rowsOf(payload, "orders").find((order) => order.order_id === orderId) ?? null;
  } catch {
    return null;
  }
}

export async function cancelOrder({ client, env, ledger, confirm, orderId, marketTicker }) {
  assertCanMutate(confirm, env);
  assertOrderId(orderId);
  const existing = await findOrder(client, orderId, marketTicker);
  const result = await client.cancelOrder(orderId, {
    market_ticker: marketTicker,
    exchange_index: -1,
  });
  const clientOrderId = existing?.client_order_id || result?.client_order_id;
  if (ledger && clientOrderId && existing) {
    ledger.release(clientOrderId, orderNotional(existing));
  }
  return {
    cancelled: true,
    result,
    note: "Cancelled because KALSHI_SAFE_MODE was off and confirm was true.",
  };
}

export async function decreaseOrder({ client, env, ledger, confirm, orderId, marketTicker, reduceBy }) {
  assertCanMutate(confirm, env);
  assertOrderId(orderId);
  if (!(reduceBy > 0)) throw new PolicyError("BAD_ORDER", "reduce_by must be positive.");
  const existing = await findOrder(client, orderId, marketTicker);
  const result = await client.decreaseOrder(orderId, {
    reduce_by: Number(reduceBy).toFixed(2),
    market_ticker: marketTicker,
    exchange_index: -1,
  });
  const clientOrderId = existing?.client_order_id || result?.client_order_id;
  if (ledger && clientOrderId && existing) {
    const remaining = Number(existing.remaining_count_fp ?? existing.remaining_count ?? existing.count) || 0;
    const unit = remaining > 0 ? orderNotional(existing) / remaining : 0;
    ledger.release(clientOrderId, round4(unit * Number(reduceBy)));
  }
  return {
    decreased: true,
    result,
    note: "Size reduced. This does not add risk.",
  };
}

export async function amendOrder({ client, env, ledger, lock, confirm, orderId, ticker, side, price, count }) {
  assertCanMutate(confirm, env);
  assertOrderId(orderId);
  const run = () => amendOrderLocked({ client, env, ledger, orderId, ticker, side, price, count });
  return lock ? lock(run) : run();
}

async function amendOrderLocked({ client, env, ledger, orderId, ticker, side, price, count }) {
  const caps = loadCaps(env);
  const existing = await findOrder(client, orderId, ticker);
  if (!existing) {
    throw new PolicyError("CAP", "Cannot amend: resting order not found, so a size increase cannot be cap-checked.");
  }
  const filled = Number(existing.fill_count_fp ?? existing.fill_count ?? 0) || 0;
  const newRemaining = Math.max(0, Number(count) - filled);
  const oldNotional = orderNotional(existing);
  const newNotional = side === "ask" || side === "no"
    ? round4(newRemaining * round4(1 - Number(price)))
    : round4(newRemaining * Number(price));
  const delta = round4(newNotional - oldNotional);
  if (delta > 0) {
    const book = await bookOrRefuse(client, ledger);
    const sizeReasons = tradeSizeReasons(newNotional, caps, book.history);
    if (sizeReasons.length) throw new PolicyError("CAP", sizeReasons.join("; "));
    enforceCaps({ notional: delta, ticker, book, caps, checkSize: false });
  }
  const updatedId = crypto.randomUUID();
  if (ledger && delta > 0) {
    ledger.record({ ticker, notional: delta, client_order_id: updatedId });
  }
  try {
    const result = await client.amendOrder(orderId, {
      ticker,
      side,
      price: Number(price).toFixed(4),
      count: Number(count).toFixed(2),
      client_order_id: existing.client_order_id,
      updated_client_order_id: updatedId,
      exchange_index: -1,
    });
    return { amended: true, delta, result, note: "Amend allowed only inside the caps." };
  } catch (err) {
    if (ledger && delta > 0) ledger.release(updatedId, delta);
    throw err;
  }
}
