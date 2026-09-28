import {
  BANKROLL_UTIL_MAX,
  DEPTH_STAKE_MULTIPLE,
  EVENT_DOLLAR_CAP,
  EVENT_FRACTION,
  MAX_OPEN_POSITIONS,
  drawdownScale,
  fractionalKellyDollars,
  stakeCapDollars,
  stakeModeFor,
} from "./edge.js";
import { PolicyError, parseCount, parseDollars, round4 } from "./policy.js";

export const HARD_TRADE_CEILING = 15;
export const MIN_CLOSED_TRADES = 3;

export const CAP_DEFAULTS = Object.freeze({
  sleeve_dollars: 71,
  default_dollars_per_trade: 2,
  max_dollars_per_trade: HARD_TRADE_CEILING,
  max_sleeve_fraction: 0.15,
  max_group_fraction: 0.3,
  max_daily_notional: 10,
  take_profit_return: 0.5,
  cut_loss_return: -0.4,
});

export function utcDay(now = new Date()) {
  return now.toISOString().slice(0, 10);
}

function readNumber(env, key, fallback, min, max) {
  const raw = env?.[key];
  if (raw == null || String(raw).trim() === "") return fallback;
  const n = Number(raw);
  if (!Number.isFinite(n) || n < min || n > max) return fallback;
  return n;
}

export function loadCaps(env = process.env) {
  const maxTrade = readNumber(
    env,
    "KALSHI_MAX_DOLLARS_PER_TRADE",
    CAP_DEFAULTS.max_dollars_per_trade,
    0.01,
    HARD_TRADE_CEILING,
  );
  let defaultTrade = readNumber(
    env,
    "KALSHI_DEFAULT_DOLLARS_PER_TRADE",
    CAP_DEFAULTS.default_dollars_per_trade,
    0.01,
    HARD_TRADE_CEILING,
  );
  if (defaultTrade > maxTrade) defaultTrade = maxTrade;
  return {
    sleeve_dollars: readNumber(env, "KALSHI_SLEEVE_DOLLARS", CAP_DEFAULTS.sleeve_dollars, 1, 1_000_000),
    default_dollars_per_trade: defaultTrade,
    max_dollars_per_trade: maxTrade,
    max_sleeve_fraction: readNumber(env, "KALSHI_MAX_SLEEVE_FRACTION", CAP_DEFAULTS.max_sleeve_fraction, 0.01, 1),
    max_group_fraction: readNumber(env, "KALSHI_MAX_GROUP_FRACTION", CAP_DEFAULTS.max_group_fraction, 0.01, 0.3),
    max_daily_notional: readNumber(env, "KALSHI_MAX_DAILY_NOTIONAL", CAP_DEFAULTS.max_daily_notional, 0.01, 1_000_000),
    take_profit_return: readNumber(env, "KALSHI_TAKE_PROFIT_RETURN", CAP_DEFAULTS.take_profit_return, 0.01, 10),
    cut_loss_return: readNumber(env, "KALSHI_CUT_LOSS_RETURN", CAP_DEFAULTS.cut_loss_return, -0.99, -0.01),
  };
}

export function marketCapDollars(caps) {
  return round4(caps.sleeve_dollars * caps.max_sleeve_fraction);
}

function clamp01(value) {
  if (!Number.isFinite(value)) return 0;
  if (value < 0) return 0;
  if (value > 1) return 1;
  return value;
}

export function tradeCeiling(caps) {
  const configured = Number(caps?.max_dollars_per_trade);
  const raw = Number.isFinite(configured) ? configured : HARD_TRADE_CEILING;
  return round4(Math.min(HARD_TRADE_CEILING, Math.max(0, raw)));
}

export function scaledBudget(caps, history, factor = 1) {
  const ceiling = tradeCeiling(caps);
  const base = Math.min(Number(caps.default_dollars_per_trade) || 0, ceiling);
  if (!history?.allows_scale) return round4(base);
  return round4(base + (ceiling - base) * clamp01(Number(factor)));
}

export function tradeSizeReasons(amount, caps, history) {
  const sized = round4(amount);
  const ceiling = tradeCeiling(caps);
  const allowed = scaledBudget(caps, history, 1);
  const reasons = [];
  if (sized > HARD_TRADE_CEILING) {
    reasons.push(`hard max $${HARD_TRADE_CEILING} per trade`);
  } else if (sized > ceiling) {
    reasons.push(`trade ceiling is $${ceiling}`);
  } else if (sized > allowed) {
    reasons.push(`size stays at the $${round4(baseDefault(caps))} default until fill history shows the sleeve is profitable`);
  }
  return reasons;
}

function baseDefault(caps) {
  return Math.min(Number(caps.default_dollars_per_trade) || 0, tradeCeiling(caps));
}

function sizeReasons(amount, caps, history, kellyDollars, stakeMode) {
  const sized = round4(amount);
  const ceiling = tradeCeiling(caps);
  const historyCap = history?.allows_scale ? ceiling : baseDefault(caps);
  const kellyCap = Number.isFinite(Number(kellyDollars)) ? Number(kellyDollars) : historyCap;
  const modeCap = stakeMode ? stakeCapDollars(stakeMode, history) : historyCap;
  const allowed = round4(Math.min(historyCap, modeCap, Math.max(0, kellyCap)));
  const reasons = [];
  if (sized > HARD_TRADE_CEILING) {
    reasons.push(`hard max $${HARD_TRADE_CEILING} per trade`);
  } else if (sized > ceiling) {
    reasons.push(`trade ceiling is $${ceiling}`);
  } else if (sized > historyCap) {
    reasons.push(`size stays at the $${round4(baseDefault(caps))} default until fill history shows the sleeve is profitable`);
  } else if (stakeMode === "fixed_2" && sized > modeCap) {
    reasons.push(`STAKE_MODE fixed_2 keeps this price at $${modeCap}`);
  } else if (stakeMode === "modest" && sized > modeCap) {
    reasons.push(`STAKE_MODE modest caps this price at $${modeCap}`);
  } else if (sized > allowed) {
    reasons.push(`size $${sized} is above quarter-Kelly ($${allowed})`);
  }
  return reasons;
}

export function createLock() {
  let tail = Promise.resolve();
  return function exclusive(task) {
    const run = tail.then(task, task);
    tail = run.then(() => undefined, () => undefined);
    return run;
  };
}

export function createLedger() {
  const entries = [];
  return {
    entries,
    record(entry) {
      entries.push({ ...entry, at: entry.at ?? Date.now(), released: 0 });
    },
    release(clientOrderId, notional) {
      const row = [...entries].reverse().find((item) => item.client_order_id === clientOrderId);
      if (!row) return 0;
      const open = row.notional - row.released;
      const amount = Math.min(open, notional);
      row.released = round4(row.released + amount);
      return amount;
    },
    snapshot(now = new Date()) {
      const day = utcDay(now);
      const byTicker = {};
      const byGroup = {};
      let daily = 0;
      for (const entry of entries) {
        if (utcDay(new Date(entry.at)) !== day) continue;
        const open = round4(entry.notional - entry.released);
        if (!(open > 0)) continue;
        daily = round4(daily + open);
        byTicker[entry.ticker] = round4((byTicker[entry.ticker] || 0) + open);
        if (entry.corr_group) byGroup[entry.corr_group] = round4((byGroup[entry.corr_group] || 0) + open);
      }
      return { byTicker, byGroup, daily, day };
    },
  };
}

export function openingNotional({ side, count, price, positionContracts = 0 }) {
  const contracts = Number(count);
  const px = Number(price);
  if (!(contracts > 0) || !(px > 0) || !(px < 1)) {
    throw new PolicyError("BAD_ORDER", "count and price must be a positive size under $1.");
  }
  const pos = Number(positionContracts) || 0;
  if (side === "bid") {
    const closing = Math.min(contracts, Math.max(0, -pos));
    return round4((contracts - closing) * px);
  }
  if (side === "ask") {
    const closing = Math.min(contracts, Math.max(0, pos));
    return round4((contracts - closing) * round4(1 - px));
  }
  throw new PolicyError("BAD_ORDER", "side must be bid or ask.");
}

export function groupCapDollars(caps) {
  return round4(caps.sleeve_dollars * (caps.max_group_fraction ?? CAP_DEFAULTS.max_group_fraction));
}

export function bankrollCapDollars(caps) {
  return round4(caps.sleeve_dollars * BANKROLL_UTIL_MAX);
}

export function eventCapDollars(caps) {
  return round4(Math.min(EVENT_DOLLAR_CAP, caps.sleeve_dollars * EVENT_FRACTION));
}

export function deployedDollars(exposure = {}) {
  return round4(Object.values(exposure).reduce((sum, value) => sum + (Number(value) > 0 ? Number(value) : 0), 0));
}

export function openPositionCount(exposure = {}) {
  return Object.values(exposure).filter((value) => Number(value) > 0).length;
}

export function checkBuyCaps({
  notional,
  marketExposure = 0,
  dailyNotional = 0,
  caps,
  history,
  checkSize = true,
  kellyDollars,
  corrGroup,
  groupExposure = 0,
  stakeMode,
  bankrollDeployed,
  openPositions,
  eventTicker,
  eventExposure = 0,
}) {
  const amount = round4(notional);
  const marketCap = marketCapDollars(caps);
  const nextMarket = round4(marketExposure + amount);
  const nextDaily = round4(dailyNotional + amount);
  const reasons = [];
  if (!(amount > 0)) reasons.push("notional must be positive");
  if (checkSize) reasons.push(...sizeReasons(amount, caps, history, kellyDollars, stakeMode));
  if (openPositions != null && !(marketExposure > 0) && openPositions >= MAX_OPEN_POSITIONS) {
    reasons.push(`open positions are ${openPositions}, cap is ${MAX_OPEN_POSITIONS}`);
  }
  if (bankrollDeployed != null) {
    const utilCap = bankrollCapDollars(caps);
    const nextBook = round4(bankrollDeployed + amount);
    if (nextBook > utilCap) {
      reasons.push(`bankroll_util would be $${nextBook}, cap is $${utilCap} (40% of the sleeve)`);
    }
  }
  if (eventTicker) {
    const nextEvent = round4(eventExposure + amount);
    const eventCap = eventCapDollars(caps);
    if (nextEvent > eventCap) {
      reasons.push(`event ${eventTicker} would be $${nextEvent}, cap is $${eventCap}`);
    }
  }
  if (corrGroup) {
    const nextGroup = round4(groupExposure + amount);
    const groupCap = groupCapDollars(caps);
    if (nextGroup > groupCap) {
      reasons.push(`corr_group ${corrGroup} would be $${nextGroup}, cap is $${groupCap}`);
    }
  }
  if (nextMarket > marketCap) {
    reasons.push(`market would be $${nextMarket}, cap is $${marketCap} (${caps.max_sleeve_fraction * 100}% of the $${caps.sleeve_dollars} sleeve)`);
  }
  if (nextMarket >= caps.sleeve_dollars) {
    reasons.push("refusing all-in: one market cannot take the whole sleeve");
  }
  if (nextDaily > caps.max_daily_notional) {
    reasons.push(`daily notional would be $${nextDaily}, cap is $${caps.max_daily_notional}`);
  }
  return {
    ok: reasons.length === 0,
    reasons,
    notional: amount,
    market_cap: marketCap,
    market_after: nextMarket,
    daily_after: nextDaily,
  };
}

export function contractsForBudget(dollars, stake) {
  if (!(dollars > 0) || !(stake > 0)) return 0;
  let contracts = Math.floor((dollars / stake) * 100) / 100;
  while (contracts > 0 && round4(contracts * stake) > dollars) {
    contracts = round4(contracts - 0.01);
  }
  return contracts > 0 ? contracts : 0;
}

export function sizeIdeas(ideas, {
  caps,
  marketExposure = {},
  dailyNotional = 0,
  history,
  groupExposure = {},
  eventExposure = {},
  drawdown = 0,
  oosCalibration = false,
} = {}) {
  const used = { ...marketExposure };
  const groups = { ...groupExposure };
  const events = { ...eventExposure };
  let dailyLeft = round4(caps.max_daily_notional - dailyNotional);
  let deployed = deployedDollars(used);
  let openCount = openPositionCount(used);
  const recommendations = [];
  const unsized = [];
  for (const idea of ideas) {
    const already = used[idea.ticker] || 0;
    const isNew = !(already > 0);
    if (isNew && openCount >= MAX_OPEN_POSITIONS) {
      unsized.push({
        ...idea,
        suggested_dollars: 0,
        suggested_contracts: 0,
        blocked_by: "open_positions",
      });
      continue;
    }
    const mode = idea.stake_mode || stakeModeFor(idea.stake);
    const modeCap = Math.min(stakeCapDollars(mode, history), tradeCeiling(caps));
    const marketRoom = round4(marketCapDollars(caps) - already);
    const group = idea.corr_group || "";
    const groupAlready = group ? (groups[group] || 0) : 0;
    const kellyDollars = fractionalKellyDollars({
      p: idea.confidence,
      price: idea.stake,
      bankroll: caps.sleeve_dollars,
      groupAlreadyOpen: groupAlready > 0,
      drawdown,
      oosCalibration,
    });
    const groupRoom = group ? round4(groupCapDollars(caps) - groupAlready) : modeCap;
    const eventKey = idea.event_ticker || "";
    const eventRoom = eventKey
      ? round4(eventCapDollars(caps) - (events[eventKey] || 0))
      : Number.POSITIVE_INFINITY;
    const bankrollRoom = round4(bankrollCapDollars(caps) - deployed);
    const depthContracts = idea.depth_at_limit > 0 ? idea.depth_at_limit / DEPTH_STAKE_MULTIPLE : null;
    const depthDollars = depthContracts == null ? modeCap : round4(depthContracts * idea.stake);
    const budget = round4(Math.min(
      kellyDollars,
      modeCap,
      marketRoom,
      dailyLeft,
      groupRoom,
      eventRoom,
      bankrollRoom,
      depthDollars,
    ));
    let contracts = contractsForBudget(budget, idea.stake);
    if (depthContracts != null) contracts = Math.min(contracts, Math.floor(depthContracts * 100) / 100);
    const notional = contracts > 0 ? round4(contracts * idea.stake) : 0;
    if (!(notional > 0)) {
      unsized.push({
        ...idea,
        stake_mode: mode,
        suggested_dollars: 0,
        suggested_contracts: 0,
        blocked_by: blockReason({
          kellyDollars,
          groupRoom,
          dailyLeft,
          marketRoom,
          drawdown,
          bankrollRoom,
          eventRoom,
        }),
      });
      continue;
    }
    dailyLeft = round4(dailyLeft - notional);
    deployed = round4(deployed + notional);
    used[idea.ticker] = round4(already + notional);
    if (isNew) openCount += 1;
    if (group) groups[group] = round4(groupAlready + notional);
    if (eventKey) events[eventKey] = round4((events[eventKey] || 0) + notional);
    recommendations.push({
      ...idea,
      stake_mode: mode,
      kelly_dollars: kellyDollars,
      suggested_dollars: notional,
      suggested_contracts: contracts,
    });
  }
  return { recommendations, unsized, bankroll_deployed: deployed, open_positions: openCount };
}

function blockReason({ kellyDollars, groupRoom, dailyLeft, marketRoom, drawdown, bankrollRoom, eventRoom }) {
  if (drawdownScale(drawdown) === 0) return "drawdown";
  if (!(kellyDollars > 0)) return "thin_edge";
  const rooms = [
    ["bankroll_util", bankrollRoom],
    ["max_per_event", eventRoom],
    ["corr_group", groupRoom],
    ["daily_cap", dailyLeft],
    ["market_cap", marketRoom],
  ];
  rooms.sort((a, b) => a[1] - b[1]);
  return rooms[0][0];
}

export function reviewPosition(row, rules) {
  const cost = Number(row.cost);
  const mark = Number(row.mark);
  const base = { ticker: row.ticker, cost, mark, hold_to_settle: true };
  if (row.falsifier_hit === true) {
    return { ...base, action: "cut", reason: "falsifier hit; exit rather than hold to settlement" };
  }
  if (row.edge_net_cents != null && row.edge_net_cents !== "" && Number(row.edge_net_cents) <= -2) {
    return {
      ...base,
      action: "cut",
      reason: "edge_net flipped to EXIT_EDGE_GONE",
      edge_net_cents: Number(row.edge_net_cents),
    };
  }
  if (!(cost > 0) || !Number.isFinite(mark)) {
    return { ...base, action: "hold", reason: "hold to settlement; missing cost or mark" };
  }
  const pnl = round4(mark - cost);
  const ret = round4(pnl / cost);
  if (row.edge_net_cents != null && row.edge_net_cents !== "" && Number(row.edge_net_cents) >= 0 && Number(row.edge_net_cents) < 1 && ret > 0) {
    return {
      ...base,
      action: "take_profit",
      reason: "remaining edge_net is inside the holding cost",
      pnl,
      return: ret,
      edge_net_cents: Number(row.edge_net_cents),
    };
  }
  if (ret <= rules.cut_loss_return) {
    return { ...base, action: "cut", reason: `return ${ret} is at or below ${rules.cut_loss_return}`, pnl, return: ret };
  }
  return { ...base, action: "hold", reason: "hold to settlement", pnl, return: ret };
}

export function reviewPositions(rows, rules) {
  return rows.map((row) => reviewPosition(row, rules));
}

function rowsOf(payload, ...keys) {
  if (Array.isArray(payload)) return payload;
  for (const key of keys) {
    if (Array.isArray(payload?.[key])) return payload[key];
  }
  return [];
}

function stampDay(row) {
  const raw = row?.created_time || row?.created_at;
  if (typeof raw === "string" && raw.length >= 10) return raw.slice(0, 10);
  const ts = row?.created_ts ?? row?.ts;
  if (ts == null || ts === "") return null;
  const n = Number(ts);
  if (!Number.isFinite(n)) return null;
  return new Date(n < 1e12 ? n * 1000 : n).toISOString().slice(0, 10);
}

export function signedContracts(row) {
  if (!row) return 0;
  return parseCount(row.position_fp ?? row.position) ?? 0;
}

function exposureOf(row) {
  const dollars = parseDollars(row.market_exposure_dollars);
  if (dollars != null) return Math.abs(dollars);
  if (row.market_exposure != null && row.market_exposure !== "") {
    const cents = Number(row.market_exposure);
    if (Number.isFinite(cents)) return round4(Math.abs(cents) / 100);
  }
  return 0;
}

export function orderNotional(order) {
  const count = parseCount(order.remaining_count_fp ?? order.remaining_count ?? order.count_fp ?? order.count) ?? 0;
  const yes = parseDollars(order.yes_price_dollars ?? order.price) ?? (
    order.yes_price != null ? round4(Number(order.yes_price) / 100) : null
  );
  if (!(count > 0) || yes == null) return 0;
  const side = String(order.side || "").toLowerCase();
  if (side === "no") return round4(count * round4(1 - yes));
  if (side === "ask") return round4(count * round4(1 - yes));
  return round4(count * yes);
}

function isNewRiskOrder(order) {
  const status = String(order.status || "").toLowerCase();
  if (status && !["resting", "open", "pending"].includes(status)) return false;
  const action = String(order.action || "").toLowerCase();
  if (action === "sell") return false;
  if (action === "buy") return true;
  const side = String(order.side || "").toLowerCase();
  return side === "bid" || side === "yes" || side === "no";
}

function fillNotional(fill) {
  const count = parseCount(fill.count_fp ?? fill.count) ?? 0;
  const yes = parseDollars(fill.yes_price_dollars) ?? (
    fill.yes_price != null ? round4(Number(fill.yes_price) / 100) : null
  );
  if (!(count > 0) || yes == null) return 0;
  const side = String(fill.side || "yes").toLowerCase();
  if (side === "no") return round4(count * round4(1 - yes));
  return round4(count * yes);
}

function dollarField(row, dollarKey, centKey) {
  if (!row) return null;
  if (row[dollarKey] != null && row[dollarKey] !== "") {
    const parsed = parseDollars(row[dollarKey]);
    if (parsed != null) return parsed;
  }
  if (row[centKey] != null && row[centKey] !== "") {
    const cents = Number(row[centKey]);
    if (Number.isFinite(cents)) return round4(cents / 100);
  }
  return null;
}

export function assessWinHistory({ positions, fills } = {}) {
  const posRows = rowsOf(positions, "market_positions", "positions");
  const fillRows = rowsOf(fills, "fills");
  const sellCount = fillRows.filter((fill) => String(fill.action || "").toLowerCase() === "sell").length;
  let realizedPositions = 0;
  let net = 0;
  let sawPnl = false;
  for (const row of posRows) {
    const pnl = dollarField(row, "realized_pnl_dollars", "realized_pnl");
    if (pnl == null) continue;
    sawPnl = true;
    if (pnl !== 0) realizedPositions += 1;
    const fees = dollarField(row, "fees_paid_dollars", "fees_paid") ?? 0;
    net = round4(net + pnl - fees);
  }
  if (!sawPnl) {
    for (const fill of fillRows) {
      const pnl = dollarField(fill, "realized_pnl_dollars", "realized_pnl");
      if (pnl == null) continue;
      const fees = dollarField(fill, "fee_cost_dollars", "fee_cost")
        ?? dollarField(fill, "fees_paid_dollars", "fees_paid")
        ?? 0;
      net = round4(net + pnl - fees);
    }
  }
  const closed = Math.max(sellCount, realizedPositions);
  return {
    closed_trades: closed,
    net_realized: net,
    allows_scale: closed >= MIN_CLOSED_TRADES && net > 0,
    min_closed_trades: MIN_CLOSED_TRADES,
  };
}

export function closedHistory() {
  return assessWinHistory({});
}

export function summarizeBook({ positions, fills, orders, ledger, now = new Date() }) {
  const byTicker = {};
  const byGroup = {};
  const contractsByTicker = {};
  const seen = new Set();
  let daily = 0;
  const day = utcDay(now);

  for (const row of rowsOf(positions, "market_positions", "positions")) {
    if (!row?.ticker) continue;
    byTicker[row.ticker] = round4((byTicker[row.ticker] || 0) + exposureOf(row));
    contractsByTicker[row.ticker] = signedContracts(row);
  }

  for (const order of rowsOf(orders, "orders")) {
    if (!isNewRiskOrder(order)) continue;
    const id = order.client_order_id;
    if (id) seen.add(id);
    const notional = orderNotional(order);
    if (order.ticker) byTicker[order.ticker] = round4((byTicker[order.ticker] || 0) + notional);
    if (stampDay(order) === day) daily = round4(daily + notional);
  }

  for (const fill of rowsOf(fills, "fills")) {
    const action = String(fill.action || "").toLowerCase();
    if (action === "sell") continue;
    if (stampDay(fill) !== day) continue;
    if (fill.client_order_id) seen.add(fill.client_order_id);
    daily = round4(daily + fillNotional(fill));
  }

  const snap = ledger?.snapshot?.(now);
  if (snap) {
    for (const entry of ledger.entries) {
      if (utcDay(new Date(entry.at)) !== day) continue;
      if (seen.has(entry.client_order_id)) continue;
      const open = round4(entry.notional - entry.released);
      if (!(open > 0)) continue;
      daily = round4(daily + open);
      byTicker[entry.ticker] = round4((byTicker[entry.ticker] || 0) + open);
      if (entry.corr_group) byGroup[entry.corr_group] = round4((byGroup[entry.corr_group] || 0) + open);
    }
  }

  return {
    byTicker,
    byGroup,
    byEvent: {},
    contractsByTicker,
    daily,
    day,
    history: assessWinHistory({ positions, fills }),
  };
}

export async function loadRiskBook(client, ledger, now = new Date()) {
  const [positions, fills, orders] = await Promise.all([
    client.getPositions ? client.getPositions({}) : {},
    client.getFills ? client.getFills({ limit: 200 }) : {},
    client.getOrders ? client.getOrders({ status: "resting", limit: 200 }) : {},
  ]);
  return summarizeBook({ positions, fills, orders, ledger, now });
}
