import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { createLedger, createLock, loadCaps, reviewPositions } from "./caps.js";
import { findBestBets } from "./scan.js";
import { isSafeMode } from "./policy.js";
import { amendOrder, cancelOrder, decreaseOrder, exitPosition, placeOrder } from "./orders.js";
import { FE_ROUTINE } from "./routine.js";

const categoryTag = z.enum(["Politics", "Crypto", "Sports", "Weather", "Finance", "Macro"]);

const signalSchema = z.object({
  key: z.string().describe("Concrete snake_case indicator, such as rcp_polling_average. Not a hunch."),
  confidence: z.number().gt(0).lte(1).describe("Model probability that this side wins. Not a win rate."),
  side: z.enum(["yes", "no"]),
  detail: z.string().min(12).max(280).describe("What was observed. A number or a specific fact."),
  market_ticker: z.string().optional(),
  event_ticker: z.string().optional(),
  series_ticker: z.string().optional(),
  category_tag: categoryTag,
  corr_group: z.string().describe("Independent risk driver, such as city_weather_week. Not a ticker."),
  model_sources: z.string().min(8).max(200),
  settlement_match_score: z.number().min(0).max(1).describe("1 only when resolution wording matches. Else no trade."),
  horizon_days: z.number().min(0).max(3650).optional(),
  falsifier: z.string().max(280).optional(),
}).strict();

const confirmField = z.boolean().describe("Must be true or the call is refused.");

function text(data) {
  return { content: [{ type: "text", text: JSON.stringify(data, null, 2) }] };
}

function failure(err) {
  const prefix = err.code ? `${err.code}: ` : "";
  return { isError: true, content: [{ type: "text", text: `${prefix}${err.message}` }] };
}

export function createServer({ client, env = process.env, ledger = createLedger(), lock = createLock() }) {
  const server = new McpServer({
    name: "kalshi-mcp",
    version: "1.3.0",
  });
  const mutate = { readOnlyHint: false, destructiveHint: true, idempotentHint: false, openWorldHint: true };
  const read = { readOnlyHint: true, destructiveHint: false, openWorldHint: true };

  server.registerTool("get_balance", {
    title: "Get balance",
    description: "Read Kalshi portfolio balance. Does not trade.",
    annotations: read,
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
    annotations: read,
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
    annotations: read,
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

  server.registerTool("get_orders", {
    title: "Get orders",
    description: "Read resting Kalshi orders. Does not trade.",
    annotations: read,
    inputSchema: {
      ticker: z.string().optional(),
      status: z.string().optional(),
      limit: z.number().int().min(1).max(200).optional(),
      cursor: z.string().optional(),
    },
  }, async ({ ticker, status, limit, cursor }) => {
    try {
      return text(await client.getOrders({ ticker, status, limit, cursor }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("find_best_bets", {
    title: "Find best bets",
    description: "Read-only scan. Gates on edge_net after the Kalshi fee dome, spread, and depth. Skips taking prices under 15 cents and buying favorites at or above 97 cents for win rate. Prefers a maker when edge is small. Sizes quarter Kelly inside about $2 until Kalshi fill history is profitable, then half Kelly up to the hard max $15, inside 15% of the sleeve, 20% per corr_group, and $10 per day. Does not place orders.",
    annotations: read,
    inputSchema: {
      signals: z.array(signalSchema).min(1).max(40),
      min_confidence: z.number().min(0).max(1).optional(),
      min_volume_24h: z.number().min(0).optional(),
      min_open_interest: z.number().min(0).optional(),
      max_spread: z.number().min(0).max(1).optional(),
      min_top_size: z.number().min(0).optional(),
      max_pages: z.number().int().min(1).max(5).optional(),
      limit: z.number().int().min(1).max(25).optional(),
      daily_notional: z.number().min(0).optional().describe("Used only when live positions cannot be loaded."),
      market_exposure: z.record(z.string(), z.number().min(0)).optional(),
    },
  }, async (args) => {
    try {
      const result = await findBestBets({
        client,
        signals: args.signals,
        options: args,
        caps: loadCaps(env),
        ledger,
      });
      return text({ ...result, safe_mode: isSafeMode(env) });
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("review_positions", {
    title: "Review positions",
    description: "Read-only end-of-day review. Default is hold to settlement. Cut when a falsifier hits or edge_net flips through the exit fee. A deep loss still cuts. Does not place or cancel.",
    annotations: read,
    inputSchema: {
      positions: z.array(z.object({
        ticker: z.string(),
        cost: z.number().nonnegative().describe("Dollars paid for the open position."),
        mark: z.number().nonnegative().describe("Dollars you could exit for now."),
        falsifier_hit: z.boolean().optional(),
        edge_net_cents: z.number().optional(),
        fee_exit_cents: z.number().nonnegative().optional(),
      }).strict()).min(1).max(50),
    },
  }, async ({ positions }) => {
    try {
      const rules = loadCaps(env);
      return text({
        rules: {
          take_profit_return: rules.take_profit_return,
          cut_loss_return: rules.cut_loss_return,
        },
        reviews: reviewPositions(positions, rules),
      });
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("place_order", {
    title: "Place order",
    description: "Place a limit order. Refused while KALSHI_SAFE_MODE is on. Refused unless confirm is true. Opening risk must clear edge_net after the fee dome. Default size is about $2 (quarter Kelly). Hard max is $15 per trade after the sleeve fill history is profitable (half Kelly). Also refused above 15% of the sleeve in one market, 20% in one corr_group, or $10 new notional per UTC day. Small edge must rest as a maker. bid buys YES. ask sells YES. There is no withdraw or deposit tool.",
    annotations: mutate,
    inputSchema: {
      confirm: confirmField,
      ticker: z.string().min(1).max(64),
      side: z.enum(["bid", "ask"]),
      count: z.number().positive().max(1000),
      price: z.number().gt(0).lt(1),
      time_in_force: z.enum(["fill_or_kill", "good_till_canceled", "immediate_or_cancel"]).optional(),
      p_model: z.number().gt(0).lte(1).optional(),
      category_tag: categoryTag.optional(),
      corr_group: z.string().optional(),
      model_sources: z.string().optional(),
      settlement_match_score: z.number().min(0).max(1).optional(),
      horizon_days: z.number().min(0).max(3650).optional(),
      drawdown_from_peak: z.number().min(0).max(1).optional(),
    },
  }, async (args) => {
    try {
      return text(await placeOrder({
        client,
        env,
        ledger,
        lock,
        confirm: args.confirm,
        ticker: args.ticker,
        side: args.side,
        count: args.count,
        price: args.price,
        timeInForce: args.time_in_force,
        research: {
          p_model: args.p_model,
          category_tag: args.category_tag,
          corr_group: args.corr_group,
          model_sources: args.model_sources,
          settlement_match_score: args.settlement_match_score,
          horizon_days: args.horizon_days,
          drawdown_from_peak: args.drawdown_from_peak,
        },
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("exit_position", {
    title: "Exit position",
    description: "Close some or all of one position. Default is hold to settlement. Refused unless a falsifier hit or edge_net flipped through the exit fee, and unless safe mode is off and confirm is true. Does not add risk and does not withdraw funds.",
    annotations: mutate,
    inputSchema: {
      confirm: confirmField,
      ticker: z.string().min(1).max(64),
      price: z.number().gt(0).lt(1).describe("YES-book price to send."),
      count: z.number().positive().max(1000).optional().describe("Contracts to close. Omit to close the whole position."),
      falsifier_hit: z.boolean().optional(),
      edge_net_cents: z.number().optional(),
    },
  }, async (args) => {
    try {
      return text(await exitPosition({
        client,
        env,
        confirm: args.confirm,
        ticker: args.ticker,
        count: args.count,
        price: args.price,
        falsifierHit: args.falsifier_hit,
        edgeNetCents: args.edge_net_cents,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("cancel_order", {
    title: "Cancel order",
    description: "Cancel a resting order. Refused unless safe mode is off and confirm is true.",
    annotations: mutate,
    inputSchema: {
      confirm: confirmField,
      order_id: z.string().min(8).max(80),
      market_ticker: z.string().min(1).max(64),
    },
  }, async (args) => {
    try {
      return text(await cancelOrder({
        client,
        env,
        ledger,
        confirm: args.confirm,
        orderId: args.order_id,
        marketTicker: args.market_ticker,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("decrease_order", {
    title: "Decrease order",
    description: "Shrink a resting order. Refused unless safe mode is off and confirm is true. Cannot increase size.",
    annotations: mutate,
    inputSchema: {
      confirm: confirmField,
      order_id: z.string().min(8).max(80),
      market_ticker: z.string().min(1).max(64),
      reduce_by: z.number().positive().max(1000),
    },
  }, async (args) => {
    try {
      return text(await decreaseOrder({
        client,
        env,
        ledger,
        confirm: args.confirm,
        orderId: args.order_id,
        marketTicker: args.market_ticker,
        reduceBy: args.reduce_by,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerTool("amend_order", {
    title: "Amend order",
    description: "Change price or size of a resting order. Refused unless safe mode is off and confirm is true. A size increase is cap-checked against the $1 default, the hard max $15, and the win-history gate. The resting order must be visible so the increase can be measured.",
    annotations: mutate,
    inputSchema: {
      confirm: confirmField,
      order_id: z.string().min(8).max(80),
      ticker: z.string().min(1).max(64),
      side: z.enum(["bid", "ask"]),
      price: z.number().gt(0).lt(1),
      count: z.number().positive().max(1000).describe("New total fillable count, including contracts already filled."),
    },
  }, async (args) => {
    try {
      return text(await amendOrder({
        client,
        env,
        ledger,
        lock,
        confirm: args.confirm,
        orderId: args.order_id,
        ticker: args.ticker,
        side: args.side,
        price: args.price,
        count: args.count,
      }));
    } catch (err) {
      return failure(err);
    }
  });

  server.registerPrompt("fe_routine", {
    title: "Finance Engineer routine",
    description: "Daily and end-of-day sleeve routine. Trade only inside caps. No withdraw or deposit.",
  }, async () => ({
    messages: [{
      role: "user",
      content: { type: "text", text: FE_ROUTINE },
    }],
  }));

  return server;
}
