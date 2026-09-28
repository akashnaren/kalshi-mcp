import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { findBestBets } from "./scan.js";
import { isSafeMode } from "./policy.js";
import { cancelOrder, placeOrder } from "./orders.js";
import { FE_ROUTINE } from "./routine.js";

const signalSchema = z.object({
  key: z.string().describe("Concrete snake_case indicator, such as rcp_polling_average. Not a hunch."),
  confidence: z.number().gt(0).lte(1).describe("Your probability that this side wins, from this indicator. Above 0 and at most 1."),
  side: z.enum(["yes", "no"]),
  detail: z.string().min(12).max(280).describe("What was observed. Numbers or a specific fact, not a vibe."),
  market_ticker: z.string().optional().describe("Most specific scope. Wins over event and series."),
  event_ticker: z.string().optional(),
  series_ticker: z.string().optional(),
}).strict();

function text(data) {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function failure(err) {
  const prefix = err.code ? `${err.code}: ` : "";
  return { isError: true, content: [{ type: "text", text: `${prefix}${err.message}` }] };
}

export function createServer({ client, env = process.env }) {
  const server = new McpServer({
    name: "kalshi-mcp",
    version: "1.0.0",
  });

  server.registerTool("get_balance", {
    title: "Get balance",
    description: "Read Kalshi portfolio balance. Does not trade.",
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    inputSchema: {},
  }, async () => {
    try {
      return text(await client.getBalance());
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("get_positions", {
    title: "Get positions",
    description: "Read open Kalshi positions. Does not trade.",
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    inputSchema: {
      ticker: z.string().optional(),
      limit: z.number().int().min(1).max(200).optional(),
      cursor: z.string().optional(),
    },
  }, async ({ ticker, limit, cursor }) => {
    try {
      return text(await client.getPositions({ ticker, limit, cursor }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("get_fills", {
    title: "Get fills",
    description: "Read recent Kalshi fills. Does not trade.",
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    inputSchema: {
      ticker: z.string().optional(),
      limit: z.number().int().min(1).max(200).optional(),
      cursor: z.string().optional(),
    },
  }, async ({ ticker, limit, cursor }) => {
    try {
      return text(await client.getFills({ ticker, limit, cursor }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("find_best_bets", {
    title: "Find best bets",
    description: "Read-only scan. Ranks contracts by confidence * payout / stake. A side is recommended only when confidence is at least 0.65 and every signal has a named indicator key plus a concrete detail. Prefers low stake and high payout. Does not place orders.",
    annotations: { readOnlyHint: true, destructiveHint: false, openWorldHint: true },
    inputSchema: {
      signals: z.array(signalSchema).min(1).max(40),
      min_confidence: z.number().min(0.65).max(1).optional().describe("Can only raise the 0.65 floor."),
      min_volume_24h: z.number().min(0).optional().describe("Default 200 contracts."),
      min_open_interest: z.number().min(0).optional().describe("Default 100 contracts."),
      max_spread: z.number().min(0).max(1).optional().describe("Max bid-ask spread in dollars. Default 0.08."),
      min_top_size: z.number().min(0).optional().describe("Contracts available at the ask. Default 10."),
      max_pages: z.number().int().min(1).max(5).optional().describe("Pages per query. Default 3."),
      limit: z.number().int().min(1).max(25).optional().describe("Rows to return. Default 8."),
    },
  }, async (args) => {
    try {
      const result = await findBestBets({
        client,
        signals: args.signals,
        options: args,
      });
      return text({ ...result, safe_mode: isSafeMode(env) });
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("place_order", {
    title: "Place order",
    description: "Mutation. Refused while KALSHI_SAFE_MODE is on (the default). Also refused unless confirm is true. find_best_bets never calls this. On the V2 book, bid buys YES and ask sells YES. Buying NO is an ask priced at one minus the NO ask.",
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    inputSchema: {
      confirm: z.boolean().describe("Must be true or the call is refused."),
      ticker: z.string().min(1).max(64),
      side: z.enum(["bid", "ask"]),
      count: z.number().positive().max(1000),
      price: z.number().gt(0).lt(1).describe("YES-book price in dollars."),
      time_in_force: z.enum(["fill_or_kill", "good_till_canceled", "immediate_or_cancel"]).optional(),
    },
  }, async (args) => {
    try {
      return text(await placeOrder({
        client,
        env,
        confirm: args.confirm,
        ticker: args.ticker,
        side: args.side,
        count: args.count,
        price: args.price,
        timeInForce: args.time_in_force,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("cancel_order", {
    title: "Cancel order",
    description: "Mutation. Refused while KALSHI_SAFE_MODE is on (the default). Also refused unless confirm is true. Does not run as part of a recommendation.",
    annotations: { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true },
    inputSchema: {
      confirm: z.boolean(),
      order_id: z.string().min(8).max(80),
      market_ticker: z.string().min(1).max(64).describe("Required so Kalshi can route the cancel to the right shard."),
    },
  }, async (args) => {
    try {
      return text(await cancelOrder({
        client,
        env,
        confirm: args.confirm,
        orderId: args.order_id,
        marketTicker: args.market_ticker,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerPrompt("fe_routine", {
    title: "Finance Engineer routine",
    description: "Akash policy prompt. Recommend only. Never auto-trade.",
  }, async () => ({
    messages: [{
      role: "user",
      content: { type: "text", text: FE_ROUTINE },
    }],
  }));

  return server;
}
