import assert from "node:assert/strict";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { test } from "node:test";

import { createServer } from "../src/index.js";

test("stdio server advertises only the read tools", async () => {
  const server = createServer();
  const client = new Client({ name: "kalshi-mcp-test", version: "0.0.0" });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([client.connect(clientTransport), server.connect(serverTransport)]);

  try {
    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name).sort();
    assert.deepEqual(names, ["get_balance", "get_fills", "get_positions"]);
    for (const tool of listed.tools) {
      assert.equal(tool.annotations?.readOnlyHint, true);
    }

    const saved = {
      id: process.env.KALSHI_API_KEY_ID,
      pem: process.env.KALSHI_PRIVATE_KEY_PEM,
      path: process.env.KALSHI_PRIVATE_KEY_PATH,
    };
    delete process.env.KALSHI_API_KEY_ID;
    delete process.env.KALSHI_PRIVATE_KEY_PEM;
    delete process.env.KALSHI_PRIVATE_KEY_PATH;
    try {
      const result = await client.callTool({ name: "get_balance", arguments: {} });
      assert.equal(result.isError, true);
      const text = JSON.stringify(result);
      assert.match(text, /KALSHI_API_KEY_ID/);
      assert.equal(text.includes("PRIVATE KEY"), false);
    } finally {
      if (saved.id === undefined) delete process.env.KALSHI_API_KEY_ID;
      else process.env.KALSHI_API_KEY_ID = saved.id;
      if (saved.pem === undefined) delete process.env.KALSHI_PRIVATE_KEY_PEM;
      else process.env.KALSHI_PRIVATE_KEY_PEM = saved.pem;
      if (saved.path === undefined) delete process.env.KALSHI_PRIVATE_KEY_PATH;
      else process.env.KALSHI_PRIVATE_KEY_PATH = saved.path;
    }
  } finally {
    await client.close();
  }
});
