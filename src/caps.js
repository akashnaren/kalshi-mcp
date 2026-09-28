import { PolicyError, parseCount, parseDollars, round4 } from "./policy.js";

export const CAP_DEFAULTS = Object.freeze({
  sleeve_dollars: 71,
  max_dollars_per_idea: 2,
  max_sleeve_fraction: 0.15,
  max_daily_notional: 10,
  likely_size_fraction: 0.5,
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
  return {
    sleeve_dollars: readNumber(env, "KALSHI_SLEEVE_DOLLARS", CAP_DEFAULTS.sleeve_dollars, 1, 1_000_000),
    max_dollars_per_idea: readNumber(env, "KALSHI_MAX_DOLLARS_PER_IDEA", CAP_DEFAULTS.max_dollars_per_idea, 0.01, 1_000_000),
    max_sleeve_fraction: readNumber(env, "KALSHI_MAX_SLEEVE_FRACTION", CAP_DEFAULTS.max_sleeve_fraction, 0.01, 1),
    max_daily_notional: readNumber(env, "KALSHI_MAX_DAILY_NOTIONAL", CAP_DEFAULTS.max_daily_notional, 0.01, 1_000_000),
    likely_size_fraction: readNumber(env, "KALSHI_LIKELY_SIZE_FRACTION", CAP_DEFAULTS.likely_size_fraction, 0.05, 1),
    take_profit_return: readNumber(env, "KALSHI_TAKE_PROFIT_RETURN", CAP_DEFAULTS.take_profit_return, 0.01, 10),
    cut_loss_return: readNumber(env, "KALSHI_CUT_LOSS_RETURN", CAP_DEFAULTS.cut_loss_return, -0.99, -0.01),
  };
}

export function marketCapDollars(caps) {
  return round4(caps.sleeve_dollars * caps.max_sleeve_fraction);
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
      let daily = 0;
      for (const entry of entries) {
        if (utcDay(new Date(entry.at)) !== day) continue;
        const open = round4(entry.notional - entry.released);
        if (!(open > 0)) continue;
        daily = round4(daily + open);
        byTicker[entry.ticker] = round4((byTicker[entry.ticker] || 0) + open);
      }
      return { byTicker, daily, day };
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

export function checkBuyCaps({ notional, marketExposure = 0, dailyNotional = 0, caps }) {
  const amount = round4(notional);
  const marketCap = marketCapDollars(caps);
  const nextMarket = round4(marketExposure + amount);
  const nextDaily = round4(dailyNotional + amount);
  const reasons = [];
  if (!(amount > 0)) reasons.push("notional must be positive");
  if (amount > caps.max_dollars_per_idea) {
    reasons.push(`idea $${amount} exceeds $${caps.max_dollars_per_idea} per idea`);
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

export function sizeIdeas(ideas, { caps, marketExposure = {}, dailyNotional = 0 }) {
  const used = { ...marketExposure };
  let dailyLeft = round4(caps.max_daily_notional - dailyNotional);
  const recommendations = [];
  const unsized = [];
  for (const idea of ideas) {
    const already = used[idea.ticker] || 0;
    const marketRoom = round4(marketCapDollars(caps) - already);
    const ideaCap = idea.lane === "likely"
      ? round4(caps.max_dollars_per_idea * caps.likely_size_fraction)
      : caps.max_dollars_per_idea;
    const budget = round4(Math.min(ideaCap, marketRoom, dailyLeft));
    const contracts = contractsForBudget(budget, idea.stake);
    const notional = contracts > 0 ? round4(contracts * idea.stake) : 0;
    if (!(notional > 0)) {
      unsized.push({
        ...idea,
        suggested_dollars: 0,
        suggested_contracts: 0,
        blocked_by: dailyLeft <= 0 ? "daily_cap" : "market_cap",
      });
      continue;
    }
    dailyLeft = round4(dailyLeft - notional);
    used[idea.ticker] = round4(already + notional);
    recommendations.push({
      ...idea,
      suggested_dollars: notional,
      suggested_contracts: contracts,
    });
  }
  return { recommendations, unsized };
}

export function reviewPosition(row, rules) {
  const cost = Number(row.cost);
  const mark = Number(row.mark);
  if (!(cost > 0) || !Number.isFinite(mark)) {
    return { ticker: row.ticker, action: "hold", reason: "missing cost or mark", cost, mark };
  }
  const pnl = round4(mark - cost);
  const ret = round4(pnl / cost);
  let action = "hold";
  let reason = "inside the hold band";
  if (ret >= rules.take_profit_return) {
    action = "take_profit";
    reason = `return ${ret} is at least ${rules.take_profit_return}`;
  } else if (ret <= rules.cut_loss_return) {
    action = "cut";
    reason = `return ${ret} is at or below ${rules.cut_loss_return}`;
  }
  return { ticker: row.ticker, action, reason, pnl, return: ret, cost, mark };
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

export function summarizeBook({ positions, fills, orders, ledger, now = new Date() }) {
  const byTicker = {};
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
    }
  }

  return { byTicker, contractsByTicker, daily, day };
}

export async function loadRiskBook(client, ledger, now = new Date()) {
  const [positions, fills, orders] = await Promise.all([
    client.getPositions ? client.getPositions({}) : {},
    client.getFills ? client.getFills({ limit: 200 }) : {},
    client.getOrders ? client.getOrders({ status: "resting", limit: 200 }) : {},
  ]);
  return summarizeBook({ positions, fills, orders, ledger, now });
}
