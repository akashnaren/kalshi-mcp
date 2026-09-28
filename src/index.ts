#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { pathToFileURL } from "node:url";
import { z } from "zod";

import { exchangeStatus, listMarkets } from "./markets.js";
import { cashOrPositions, getBalance, getFills, getPositions } from "./portfolio.js";

const limitField = z
  .number()
  .int()
  .min(1)
  .max(200)
  .optional()
  .describe("Page size from 1 to 200. Default 50.");

const cursorField = z.string().min(1).optional().describe("Pagination cursor from a previous page.");
const tickerField = z.string().min(1).optional().describe("Market ticker filter.");

const readOnly = {
  readOnlyHint: true,
  destructiveHint: false,
  openWorldHint: true,
} as const;

function textResult(data: unknown) {
  return {
    content: [{ type: "text" as const, text: JSON.stringify(data, null, 2) }],
  };
}

function toolError(err: unknown) {
  const message = err instanceof Error ? err.message : "Kalshi request failed";
  const safe = /-----BEGIN|PRIVATE KEY/.test(message) ? "Kalshi request failed" : message;
  return {
    isError: true,
    content: [{ type: "text" as const, text: safe }],
  };
}

export function createServer(): McpServer {
  const server = new McpServer({
    name: "kalshi-readonly",
    version: "0.1.0",
  });
  registerTools(server);
  return server;
}

function registerTools(server: McpServer): void {
  server.registerTool(
    "exchange_status",
    {
      title: "Exchange status",
      description: "Public Kalshi exchange status. Read-only.",
      inputSchema: {},
      annotations: readOnly,
    },
    async () => {
      try {
        return textResult(await exchangeStatus());
      } catch (err) {
        return toolError(err);
      }
    },
  );

  server.registerTool(
    "list_markets",
    {
      title: "List markets",
      description: "List Kalshi markets (public). Read-only.",
      inputSchema: {
        limit: z
          .number()
          .int()
          .min(1)
          .max(200)
          .optional()
          .describe("Page size from 1 to 200. Default 5."),
        status: z.string().min(1).optional().describe("Market status filter."),
        ticker: tickerField,
      },
      annotations: readOnly,
    },
    async ({ limit, status, ticker }) => {
      try {
        return textResult(await listMarkets({ limit, status, ticker }));
      } catch (err) {
        return toolError(err);
      }
    },
  );

  server.registerTool(
    "cash_or_positions",
    {
      title: "Cash or positions",
      description:
        "Read Kalshi cash and positions. include is balance, cash, positions, or both (default both). Returns official cash, balance_dollars, portfolio_value, and market plus event positions. Read-only; does not trade.",
      inputSchema: {
        include: z
          .enum(["balance", "cash", "positions", "both"])
          .optional()
          .describe("balance, cash, positions, or both. Default both."),
        limit: limitField,
      },
      annotations: readOnly,
    },
    async ({ include, limit }) => {
      try {
        return textResult(await cashOrPositions({ include, limit }));
      } catch (err) {
        return toolError(err);
      }
    },
  );

  server.registerTool(
    "get_balance",
    {
      title: "Get balance",
      description:
        "Read Kalshi cash in cents and dollars, plus portfolio_value when the API returns it. Read-only; does not trade.",
      inputSchema: {},
      annotations: readOnly,
    },
    async () => {
      try {
        return textResult(await getBalance());
      } catch (err) {
        return toolError(err);
      }
    },
  );

  server.registerTool(
    "get_positions",
    {
      title: "Get positions",
      description:
        "Read Kalshi market and event positions. Official ticker, quantity, and price fields only. Read-only; does not trade.",
      inputSchema: {
        limit: limitField,
        cursor: cursorField,
        ticker: tickerField,
      },
      annotations: readOnly,
    },
    async ({ limit, cursor, ticker }) => {
      try {
        return textResult(await getPositions({ limit, cursor, ticker }));
      } catch (err) {
        return toolError(err);
      }
    },
  );

  server.registerTool(
    "get_fills",
    {
      title: "Get fills",
      description:
        "Read recent Kalshi fills (ticker, side, quantity, official prices). Read-only; does not trade.",
      inputSchema: {
        limit: limitField,
        cursor: cursorField,
        ticker: tickerField,
      },
      annotations: readOnly,
    },
    async ({ limit, cursor, ticker }) => {
      try {
        return textResult(await getFills({ limit, cursor, ticker }));
      } catch (err) {
        return toolError(err);
      }
    },
  );
}

async function main(): Promise<void> {
  const transport = new StdioServerTransport();
  await createServer().connect(transport);
}

const invokedDirectly = process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;

if (invokedDirectly) {
  main().catch((err: unknown) => {
    const message = err instanceof Error ? err.message : "failed to start";
    process.stderr.write(`kalshi-mcp: ${message}\n`);
    process.exit(1);
  });
}
