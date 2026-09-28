#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { pathToFileURL } from "node:url";
import { z } from "zod";

import { getBalance, getFills, getPositions } from "./portfolio.js";

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
    name: "kalshi",
    version: "0.1.0",
  });
  registerTools(server);
  return server;
}

function registerTools(server: McpServer): void {
  server.registerTool(
    "get_balance",
    {
      title: "Get balance",
      description:
        "Read the Kalshi portfolio cash balance in cents and dollars. Read-only; does not trade.",
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
        "Read open Kalshi positions (non-zero contracts). Read-only; does not trade.",
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
      description: "Read recent Kalshi fills. Read-only; does not trade.",
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
