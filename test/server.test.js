import assert from "node:assert/strict";
import test from "node:test";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { createServer } from "../src/server.js";
import { market, signal } from "./helpers.js";

function fakeClient() {
  const calls = [];
  return {
    calls,
    async getMarkets() {
      calls.push("markets");
      return { markets: [market()], cursor: "" };
    },
    async getBalance() {
      calls.push("balance");
      return { balance: 2500 };
    },
    async getPositions() {
      calls.push("positions");
      return { market_positions: [] };
    },
    async getFills() {
      calls.push("fills");
      return { fills: [] };
    },
    async createOrder() {
      calls.push("create");
      return { order_id: "should-not-run" };
    },
    async cancelOrder() {
      calls.push("cancel");
      return { order_id: "should-not-run" };
    },
  };
}

async function withClient(env, run) {
  const kalshi = fakeClient();
  const server = createServer({ client: kalshi, env });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  const client = new Client({ name: "test", version: "0.0.0" });
  await server.connect(serverTransport);
  await client.connect(clientTransport);
  try {
    await run(client, kalshi);
  } finally {
    await client.close();
    await server.close();
  }
}

test("tool list is read-heavy and find_best_bets returns named keys", async () => {
  await withClient({ KALSHI_SAFE_MODE: "1" }, async (client, kalshi) => {
    const listed = await client.listTools();
    const names = listed.tools.map((tool) => tool.name).sort();
    assert.deepEqual(names, [
      "amend_order",
      "cancel_order",
      "decrease_order",
      "exit_position",
      "find_best_bets",
      "get_balance",
      "get_fills",
      "get_orders",
      "get_positions",
      "place_order",
      "review_positions",
    ]);
    assert.equal(names.includes("withdraw"), false);
    assert.equal(names.includes("deposit"), false);
    const finder = listed.tools.find((tool) => tool.name === "find_best_bets");
    assert.equal(finder.annotations.readOnlyHint, true);

    const result = await client.callTool({
      name: "find_best_bets",
      arguments: { signals: [signal()] },
    });
    const payload = JSON.parse(result.content[0].text);
    assert.equal(payload.recommendations.length, 1);
    assert.deepEqual(payload.recommendations[0].keys, ["nhc_cone_includes_city"]);
    assert.equal(payload.safe_mode, true);
    assert.equal(payload.policy.auto_trade, true);
    assert.equal(payload.policy.scan_places_orders, false);
    assert.equal(payload.recommendations[0].lane, "asymmetric");
    assert.ok(payload.recommendations[0].suggested_dollars <= 2);
    const balance = await client.callTool({ name: "get_balance", arguments: {} });
    assert.equal(JSON.parse(balance.content[0].text).balance, 2500);
    assert.equal(kalshi.calls.includes("create"), false);
    assert.equal(kalshi.calls.includes("cancel"), false);
    assert.equal(kalshi.calls.includes("balance"), true);
  });
});

test("default env and explicit safe mode both refuse mutations", async () => {
  for (const env of [{}, { KALSHI_SAFE_MODE: "1" }]) {
    await withClient(env, async (client, kalshi) => {
      const placed = await client.callTool({
        name: "place_order",
        arguments: {
          confirm: true,
          ticker: "KXTEST-26-T1",
          side: "bid",
          count: 1,
          price: 0.15,
        },
      });
      assert.equal(placed.isError, true);
      assert.match(placed.content[0].text, /SAFE_MODE/);
      const cancelled = await client.callTool({
        name: "cancel_order",
        arguments: { confirm: true, order_id: "abc12345", market_ticker: "KXTEST-26-T1" },
      });
      assert.equal(cancelled.isError, true);
      assert.equal(kalshi.calls.includes("create"), false);
      assert.equal(kalshi.calls.includes("cancel"), false);
    });
  }
});

test("live sleeve rejects an oversized order and still exits", async () => {
  await withClient({ KALSHI_SAFE_MODE: "0" }, async (client, kalshi) => {
    kalshi.getPositions = async () => ({
      market_positions: [{ ticker: "KXTEST-26-T1", position_fp: "4.00", market_exposure_dollars: "1.00" }],
    });
    const blocked = await client.callTool({
      name: "place_order",
      arguments: { confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 20, price: 0.5 },
    });
    assert.equal(blocked.isError, true);
    assert.match(blocked.content[0].text, /CAP/);
    assert.equal(kalshi.calls.includes("create"), false);

    const placed = await client.callTool({
      name: "place_order",
      arguments: { confirm: true, ticker: "KXOTHER-1", side: "bid", count: 4, price: 0.5 },
    });
    assert.equal(placed.isError, undefined);
    const review = await client.callTool({
      name: "review_positions",
      arguments: { positions: [{ ticker: "KXTEST-26-T1", cost: 1, mark: 1.6 }] },
    });
    const reviewed = JSON.parse(review.content[0].text);
    assert.equal(reviewed.reviews[0].action, "take_profit");

    const exited = await client.callTool({
      name: "exit_position",
      arguments: { confirm: true, ticker: "KXTEST-26-T1", price: 0.4 },
    });
    assert.equal(exited.isError, undefined);
    assert.equal(kalshi.calls.filter((call) => call === "create").length, 2);
  });
});

test("stdio entry lists tools and stays in safe mode", async () => {
  const transport = new StdioClientTransport({
    command: process.execPath,
    args: ["src/index.js"],
    cwd: new URL("..", import.meta.url).pathname,
    env: {
      PATH: process.env.PATH,
      KALSHI_SAFE_MODE: "1",
    },
    stderr: "pipe",
  });
  const client = new Client({ name: "stdio-test", version: "0.0.0" });
  await client.connect(transport);
  try {
    const listed = await client.listTools();
    assert.ok(listed.tools.some((tool) => tool.name === "find_best_bets"));
    const prompts = await client.listPrompts();
    assert.ok(prompts.prompts.some((prompt) => prompt.name === "fe_routine"));
    const refused = await client.callTool({
      name: "place_order",
      arguments: { confirm: true, ticker: "KXTEST-26-T1", side: "bid", count: 1, price: 0.2 },
    });
    assert.equal(refused.isError, true);
    assert.match(refused.content[0].text, /SAFE_MODE/);
  } finally {
    await client.close();
  }
});
