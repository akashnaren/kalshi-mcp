import { clampLimit, kalshiGet, type GetOptions, type Query } from "./client.js";

function asRecord(raw: unknown, label: string): Record<string, unknown> {
  if (!raw || typeof raw !== "object") {
    throw new Error(`unexpected ${label} payload`);
  }
  return raw as Record<string, unknown>;
}

function pick(source: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of keys) {
    if (source[key] !== undefined) out[key] = source[key];
  }
  return out;
}

function firstDefined(source: Record<string, unknown>, keys: readonly string[]): unknown {
  for (const key of keys) {
    if (source[key] !== undefined) return source[key];
  }
  return undefined;
}

/**
 * Official balance fields only. `cash` aliases `balance_dollars`.
 * `portfolio_value` is copied when the API sends it. No recovery estimate.
 */
export function presentBalance(raw: unknown): Record<string, unknown> {
  const body = asRecord(raw, "balance");
  const cents = body.balance;
  if (typeof cents !== "number" || !Number.isFinite(cents)) {
    throw new Error("balance payload missing numeric balance");
  }
  const dollars =
    typeof body.balance_dollars === "string" ? body.balance_dollars : (cents / 100).toFixed(2);
  const presented: Record<string, unknown> = {
    balance: cents,
    balance_cents: cents,
    balance_dollars: dollars,
    cash: dollars,
  };
  if ("portfolio_value" in body) presented.portfolio_value = body.portfolio_value;
  if ("updated_ts" in body) presented.updated_ts = body.updated_ts;
  if ("balance_breakdown" in body) presented.balance_breakdown = body.balance_breakdown;
  return presented;
}

const MARKET_KEYS = [
  "ticker",
  "side",
  "position",
  "position_fp",
  "total_traded",
  "total_traded_dollars",
  "market_exposure",
  "market_exposure_dollars",
  "realized_pnl",
  "realized_pnl_dollars",
  "fees_paid",
  "fees_paid_dollars",
  "resting_orders_count",
  "last_updated_ts",
  "exchange_index",
  "average_price",
  "average_price_dollars",
  "avg_price",
  "avg_price_dollars",
  "mark",
  "mark_price",
  "mark_price_dollars",
] as const;

const EVENT_KEYS = [
  "event_ticker",
  "ticker",
  "total_cost",
  "total_cost_dollars",
  "total_cost_shares",
  "total_cost_shares_fp",
  "event_exposure",
  "event_exposure_dollars",
  "realized_pnl",
  "realized_pnl_dollars",
  "fees_paid",
  "fees_paid_dollars",
] as const;

function presentMarketPosition(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object") return {};
  const source = raw as Record<string, unknown>;
  const out = pick(source, MARKET_KEYS);
  const qty = firstDefined(source, ["position_fp", "position"]);
  if (qty !== undefined) out.qty = qty;
  const avg = firstDefined(source, [
    "average_price_dollars",
    "average_price",
    "avg_price_dollars",
    "avg_price",
  ]);
  if (avg !== undefined) out.avg = avg;
  const mark = firstDefined(source, ["mark_price_dollars", "mark_price", "mark"]);
  if (mark !== undefined) out.mark = mark;
  return out;
}

function presentEventPosition(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object") return {};
  const source = raw as Record<string, unknown>;
  const out = pick(source, EVENT_KEYS);
  if (out.ticker === undefined && source.event_ticker !== undefined) out.ticker = source.event_ticker;
  const qty = firstDefined(source, ["total_cost_shares_fp", "total_cost_shares"]);
  if (qty !== undefined) out.qty = qty;
  return out;
}

export function presentPositions(raw: unknown): Record<string, unknown> {
  const body = asRecord(raw, "positions");
  const presented: Record<string, unknown> = {};
  if (Array.isArray(body.market_positions)) {
    presented.market_positions = body.market_positions.map(presentMarketPosition);
  }
  if (Array.isArray(body.event_positions)) {
    presented.event_positions = body.event_positions.map(presentEventPosition);
  }
  if ("cursor" in body) presented.cursor = body.cursor;
  return presented;
}

const FILL_KEYS = [
  "fill_id",
  "trade_id",
  "order_id",
  "ticker",
  "market_ticker",
  "side",
  "action",
  "outcome_side",
  "book_side",
  "count",
  "count_fp",
  "yes_price",
  "no_price",
  "yes_price_dollars",
  "no_price_dollars",
  "is_taker",
  "created_time",
  "ts",
  "fee_cost",
  "subaccount_number",
  "exchange_index",
] as const;

function presentFill(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object") return {};
  const source = raw as Record<string, unknown>;
  const out = pick(source, FILL_KEYS);
  if (out.ticker === undefined && source.market_ticker !== undefined) out.ticker = source.market_ticker;
  if (out.side === undefined && source.outcome_side !== undefined) out.side = source.outcome_side;
  const qty = firstDefined(source, ["count_fp", "count"]);
  if (qty !== undefined) out.qty = qty;
  return out;
}

export function presentFills(raw: unknown): Record<string, unknown> {
  const body = asRecord(raw, "fills");
  const presented: Record<string, unknown> = {};
  if (Array.isArray(body.fills)) presented.fills = body.fills.map(presentFill);
  if ("cursor" in body) presented.cursor = body.cursor;
  return presented;
}

function pageQuery(limit: number | undefined, cursor?: string, ticker?: string): Query {
  return {
    limit: clampLimit(limit),
    cursor,
    ticker,
  };
}

export async function getBalance(opts?: GetOptions) {
  return presentBalance(await kalshiGet("/portfolio/balance", undefined, opts));
}

export async function getPositions(
  args: { limit?: number; cursor?: string; ticker?: string } = {},
  opts?: GetOptions,
) {
  return presentPositions(
    await kalshiGet("/portfolio/positions", pageQuery(args.limit, args.cursor, args.ticker), opts),
  );
}

export async function getFills(
  args: { limit?: number; cursor?: string; ticker?: string } = {},
  opts?: GetOptions,
) {
  return presentFills(
    await kalshiGet("/portfolio/fills", pageQuery(args.limit, args.cursor, args.ticker), opts),
  );
}

/** Same include/limit contract as the local kalshi-readonly plugin. */
export async function cashOrPositions(
  args: { include?: string; limit?: number } = {},
  opts?: GetOptions,
): Promise<Record<string, unknown>> {
  const want = (args.include ?? "both").toLowerCase();
  const out: Record<string, unknown> = {};
  if (want === "balance" || want === "both" || want === "cash") {
    out.balance = await getBalance(opts);
  }
  if (want === "positions" || want === "both") {
    out.positions = await getPositions({ limit: args.limit }, opts);
  }
  return out;
}
