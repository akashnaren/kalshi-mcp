import { kalshiGet, type GetOptions, type Query } from "./client.js";

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

export function clampLimit(limit: number | undefined, fallback = DEFAULT_LIMIT): number {
  if (limit === undefined) return fallback;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new Error(`limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  return limit;
}

export function presentBalance(raw: unknown): {
  balance_cents: number;
  balance_dollars: string;
  portfolio_value_cents?: number;
  portfolio_value_dollars?: string;
  updated_ts?: unknown;
  balance_breakdown?: unknown;
} {
  if (!raw || typeof raw !== "object") {
    throw new Error("unexpected balance payload");
  }
  const body = raw as Record<string, unknown>;
  const cents = body.balance;
  if (typeof cents !== "number" || !Number.isFinite(cents)) {
    throw new Error("balance payload missing numeric balance");
  }
  const dollars =
    typeof body.balance_dollars === "string" ? body.balance_dollars : (cents / 100).toFixed(2);
  const presented: {
    balance_cents: number;
    balance_dollars: string;
    portfolio_value_cents?: number;
    portfolio_value_dollars?: string;
    updated_ts?: unknown;
    balance_breakdown?: unknown;
  } = {
    balance_cents: cents,
    balance_dollars: dollars,
  };
  if (typeof body.portfolio_value === "number" && Number.isFinite(body.portfolio_value)) {
    presented.portfolio_value_cents = body.portfolio_value;
    presented.portfolio_value_dollars = (body.portfolio_value / 100).toFixed(2);
  }
  if ("updated_ts" in body) presented.updated_ts = body.updated_ts;
  if ("balance_breakdown" in body) presented.balance_breakdown = body.balance_breakdown;
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
  return kalshiGet(
    "/portfolio/positions",
    { ...pageQuery(args.limit, args.cursor, args.ticker), count_filter: "position" },
    opts,
  );
}

export async function getFills(
  args: { limit?: number; cursor?: string; ticker?: string } = {},
  opts?: GetOptions,
) {
  return kalshiGet("/portfolio/fills", pageQuery(args.limit, args.cursor, args.ticker), opts);
}
