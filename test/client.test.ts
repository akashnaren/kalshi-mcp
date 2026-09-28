import assert from "node:assert/strict";
import crypto from "node:crypto";
import { test } from "node:test";

import { getBalance, getFills, getPositions } from "../src/portfolio.js";
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
    balance_cents: 250,
    balance_dollars: "2.5000",
    portfolio_value_cents: 0,
    portfolio_value_dollars: "0.00",
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
  assert.equal(opts.calls[0]?.url.searchParams.get("count_filter"), "position");
  assert.equal(opts.calls[1]?.url.pathname, "/trade-api/v2/portfolio/fills");
  assert.equal(opts.calls[1]?.url.searchParams.get("limit"), "50");
  assert.equal(opts.calls[1]?.url.searchParams.get("ticker"), "KXTEST");
  assert.deepEqual(
    opts.calls.map((call) => call.init.method),
    ["GET", "GET"],
  );
});
