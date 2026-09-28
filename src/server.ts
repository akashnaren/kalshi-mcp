import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import type { PythonBridge } from "./python.js";

interface ListedTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

function safeText(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.includes("PRIVATE KEY") || message.includes("-----BEGIN") ? "Kalshi request failed" : message;
}

/**
 * MCP server. Tool bodies run in the Python worker. stdout stays on the SDK transport.
 * `tools` may still be loading. initialize can finish before that list resolves.
 * The list is fixed for this process (`listChanged` stays false); restart the host
 * after changing KALSHI_SAFE_MODE.
 */
export function createServer(bridge: PythonBridge, tools: ListedTool[] | Promise<ListedTool[]>): Server {
  const ready = Promise.resolve(tools);
  const server = new Server(
    { name: "kalshi-readonly", version: "0.3.0" },
    { capabilities: { tools: { listChanged: false } } },
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    try {
      return { tools: await ready };
    } catch (error) {
      throw new Error(safeText(error));
    }
  });
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const name = request.params.name;
    const args = request.params.arguments ?? {};
    try {
      const response = await bridge.request({ op: "call", name, arguments: args });
      return {
        content: [{ type: "text" as const, text: JSON.stringify(response.result ?? {}, null, 2) }],
      };
    } catch (error) {
      return {
        content: [{ type: "text" as const, text: `error: ${safeText(error)}` }],
        isError: true,
      };
    }
  });
  return server;
}
