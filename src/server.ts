import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";
import type { PythonBridge } from "./python.js";

interface ListedTool {
  name: string;
  description?: string;
  inputSchema?: Record<string, unknown>;
}

/** MCP server. Tool bodies run in the Python worker. stdout stays on the SDK transport. */
export function createServer(bridge: PythonBridge, tools: ListedTool[]): Server {
  const server = new Server(
    { name: "kalshi-readonly", version: "0.3.0" },
    { capabilities: { tools: { listChanged: false } } },
  );
  server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }));
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const name = request.params.name;
    const args = request.params.arguments ?? {};
    try {
      const response = await bridge.request({ op: "call", name, arguments: args });
      return {
        content: [{ type: "text" as const, text: JSON.stringify(response.result ?? {}, null, 2) }],
      };
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      const safe = message.includes("PRIVATE KEY") || message.includes("-----BEGIN") ? "Kalshi request failed" : message;
      return {
        content: [{ type: "text" as const, text: `error: ${safe}` }],
        isError: true,
      };
    }
  });
  return server;
}
