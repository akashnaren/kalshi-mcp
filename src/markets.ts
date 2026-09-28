import { clampLimit, kalshiPublicGet, type GetOptions } from "./client.js";

const COMPACT_KEYS = ["ticker", "title", "status", "yes_bid", "yes_ask", "volume"] as const;
const EXTRA_KEYS = ["yes_bid_dollars", "yes_ask_dollars", "volume_fp"] as const;

export function presentMarkets(raw: unknown): {
  count: number;
  markets: Record<string, unknown>[];
  cursor: unknown;
} {
  const body = raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
  const markets = Array.isArray(body.markets) ? body.markets : [];
  const compact = markets.map((item) => {
    const source = item && typeof item === "object" ? (item as Record<string, unknown>) : {};
    const row: Record<string, unknown> = {};
    for (const key of COMPACT_KEYS) row[key] = source[key] ?? null;
    for (const key of EXTRA_KEYS) {
      if (source[key] !== undefined) row[key] = source[key];
    }
    return row;
  });
  return { count: compact.length, markets: compact, cursor: body.cursor ?? null };
}

export async function exchangeStatus(opts?: GetOptions) {
  return kalshiPublicGet("/exchange/status", undefined, opts);
}

export async function listMarkets(
  args: { limit?: number; status?: string; ticker?: string } = {},
  opts?: GetOptions,
) {
  const data = await kalshiPublicGet(
    "/markets",
    {
      limit: clampLimit(args.limit, 5),
      status: args.status,
      ticker: args.ticker,
    },
    opts,
  );
  return presentMarkets(data);
}
