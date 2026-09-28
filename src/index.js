#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { KalshiClient } from "./client.js";
import { loadConfig } from "./config.js";
import { createServer } from "./server.js";

const config = loadConfig(process.env);
const client = new KalshiClient({
  baseUrl: config.baseUrl,
  apiKeyId: config.apiKeyId,
  privateKeyPath: config.privateKeyPath,
});
const server = createServer({ client, env: process.env });

process.stderr.write(`kalshi-mcp stdio ready safe_mode=${config.safeMode ? "on" : "off"}\n`);
await server.connect(new StdioServerTransport());
