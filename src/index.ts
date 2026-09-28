#!/usr/bin/env node
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { PythonBridge, repoRoot, type WorkerResponse } from "./python.js";
import { createServer } from "./server.js";

function safeModeOn(): boolean {
  const raw = (process.env.KALSHI_SAFE_MODE ?? "1").trim().toLowerCase();
  return !["0", "false", "no", "off"].includes(raw);
}

function toolList(response: WorkerResponse): { name: string; description?: string; inputSchema?: Record<string, unknown> }[] {
  if (!Array.isArray(response.tools)) {
    throw new Error("python worker did not return tools");
  }
  return response.tools.filter((tool): tool is { name: string; description?: string; inputSchema?: Record<string, unknown> } => {
    return typeof tool === "object" && tool !== null && typeof (tool as { name?: unknown }).name === "string";
  });
}

async function main(): Promise<void> {
  const bridge = new PythonBridge(repoRoot());
  const listed = await bridge.request({ op: "list" }, 20_000);
  const server = createServer(bridge, toolList(listed));
  await server.connect(new StdioServerTransport());
  console.error(`kalshi-readonly node safeMode=${safeModeOn() ? "on" : "off"}`);
}

main().catch((error: unknown) => {
  const message = error instanceof Error ? error.message : String(error);
  console.error("Fatal:", message.includes("PRIVATE KEY") || message.includes("-----BEGIN") ? "Kalshi request failed" : message);
  process.exit(1);
});
