import assert from "node:assert/strict";
import crypto from "node:crypto";
import { test } from "node:test";

import { exchangeStatus, listMarkets } from "../src/markets.js";
import { cashOrPositions, getBalance, getFills, getPositions } from "../src/portfolio.js";
import type { GetOptions } from "../src/client.js";

function keyPair() {
  return crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
}

function options(handler: (url: URL, init: RequestInit) => unknown): GetOptions & {
  calls: { url: URL; init: RequestInit }[];
} {
  const { privateKey, publicKey } = keyPair();
  const pem = privateKey.export({ type: "pkcs8", format: "pem" }).toString();
  const calls: { url: URL; init: RequestInit }[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    const request = init ?? {};
    calls.push({ url, init: request });
    const headers = new Headers(request.headers);
    const timestamp = headers.get("KALSHI-ACCESS-TIMESTAMP") ?? "";
    const signature = headers.get("KALSHI-ACCESS-SIGNATURE") ?? "";
    const ok = crypto.verify(
      "sha256",
      Buffer.from(`${timestamp}GET${url.pathname}`, "utf8"),
      {
        key: publicKey,
        padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
        saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
      },
      Buffer.from(signature, "base64"),
    );
    assert.equal(ok, true);
    assert.equal(request.method, "GET");
    const body = handler(url, request);
    return new Response(JSON.stringify(body), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  return {
    calls,
    auth: { keyId: "key-123", privateKeyPem: pem },
    base: "https://api.elections.kalshi.com/trade-api/v2",
    nowMs: 1703123456789,
    fetchImpl,
  };
}

test("getBalance signs the portfolio path and returns cents plus dollars", async () => {
  const opts = options(() => ({
    balance: 250,
    balance_dollars: "2.5000",
    portfolio_value: 0,
    updated_ts: 1,
  }));
  const balance = await getBalance(opts);
  assert.deepEqual(balance, {
    balance: 250,
    balance_cents: 250,
    balance_dollars: "2.5000",
    cash: "2.5000",
    portfolio_value: 0,
    updated_ts: 1,
  });
  assert.equal(opts.calls[0]?.url.pathname, "/trade-api/v2/portfolio/balance");
  assert.equal(opts.calls[0]?.init.method, "GET");
  const headers = new Headers(opts.calls[0]?.init.headers);
  assert.equal(headers.get("KALSHI-ACCESS-KEY"), "key-123");
  assert.equal(JSON.stringify(opts.calls).includes("PRIVATE KEY"), false);
});

test("positions and fills stay on GET with the limit query", async () => {
  const opts = options((url) => ({ path: url.pathname, limit: url.searchParams.get("limit") }));
  await getPositions({ limit: 10 }, opts);
  await getFills({ ticker: "KXTEST" }, opts);
  assert.equal(opts.calls[0]?.url.pathname, "/trade-api/v2/portfolio/positions");
  assert.equal(opts.calls[0]?.url.searchParams.get("limit"), "10");
  assert.equal(opts.calls[0]?.url.searchParams.has("count_filter"), false);
  assert.equal(opts.calls[1]?.url.pathname, "/trade-api/v2/portfolio/fills");
  assert.equal(opts.calls[1]?.url.searchParams.get("limit"), "50");
  assert.equal(opts.calls[1]?.url.searchParams.get("ticker"), "KXTEST");
  assert.deepEqual(
    opts.calls.map((call) => call.init.method),
    ["GET", "GET"],
  );
});

test("cash_or_positions matches the local include contract", async () => {
  const opts = options((url) => {
    if (url.pathname.endsWith("/balance")) {
      return { balance: 100, balance_dollars: "1.0000", portfolio_value: 20 };
    }
    return { market_positions: [{ ticker: "KXTEST", position_fp: "1.00" }], event_positions: [], cursor: "" };
  });
  const report = await cashOrPositions({ include: "both", limit: 50 }, opts);
  assert.equal(opts.calls.length, 2);
  assert.equal(opts.calls[0]?.url.pathname, "/trade-api/v2/portfolio/balance");
  assert.equal(opts.calls[1]?.url.pathname, "/trade-api/v2/portfolio/positions");
  assert.equal(opts.calls[1]?.url.searchParams.get("limit"), "50");
  const balance = report.balance as Record<string, unknown>;
  assert.equal(balance.cash, "1.0000");
  assert.equal(balance.balance_dollars, "1.0000");
  assert.equal(balance.portfolio_value, 20);
  const positions = report.positions as { market_positions: { ticker: string; qty: string }[] };
  assert.equal(positions.market_positions[0]?.ticker, "KXTEST");
  assert.equal(positions.market_positions[0]?.qty, "1.00");
  assert.equal(JSON.stringify(report).includes("remaining"), false);

  const cashOnly = options(() => ({ balance: 5, balance_dollars: "0.0500" }));
  const cash = await cashOrPositions({ include: "cash" }, cashOnly);
  assert.equal(cashOnly.calls.length, 1);
  assert.equal(cashOnly.calls[0]?.url.pathname, "/trade-api/v2/portfolio/balance");
  assert.equal("positions" in cash, false);
});

test("public market tools do not send signed headers", async () => {
  const calls: { url: URL; init: RequestInit }[] = [];
  const fetchImpl: typeof fetch = async (input, init) => {
    const url = new URL(String(input));
    calls.push({ url, init: init ?? {} });
    const body = url.pathname.endsWith("/markets")
      ? { markets: [{ ticker: "KXTEST", title: "Test", status: "open" }], cursor: null }
      : { exchange_active: true };
    return new Response(JSON.stringify(body), { status: 200 });
  };
  const opts = { base: "https://api.elections.kalshi.com/trade-api/v2", fetchImpl };
  await exchangeStatus(opts);
  await listMarkets({ limit: 5, status: "open" }, opts);
  assert.equal(calls[0]?.url.pathname, "/trade-api/v2/exchange/status");
  assert.equal(calls[1]?.url.pathname, "/trade-api/v2/markets");
  assert.equal(calls[1]?.url.searchParams.get("limit"), "5");
  assert.equal(calls[1]?.url.searchParams.get("status"), "open");
  for (const call of calls) {
    const headers = new Headers(call.init.headers);
    assert.equal(call.init.method, "GET");
    assert.equal(headers.get("KALSHI-ACCESS-KEY"), null);
    assert.equal(headers.get("KALSHI-ACCESS-SIGNATURE"), null);
  }
});
