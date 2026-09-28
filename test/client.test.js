import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { KalshiClient } from "../src/client.js";

function pemKey() {
  const { privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  return privateKey.export({ type: "pkcs8", format: "pem" });
}

test("authenticated requests sign the path without the query string", async () => {
  const seen = [];
  const client = new KalshiClient({
    baseUrl: "https://external-api.kalshi.com/trade-api/v2",
    apiKeyId: "key-id",
    privateKeyPem: pemKey(),
    fetchImpl: async (url, init) => {
      seen.push({ url: String(url), init });
      return new Response(JSON.stringify({ balance: 100 }), { status: 200 });
    },
  });
  const body = await client.getPositions({ limit: 5, cursor: "" });
  assert.deepEqual(body, { balance: 100 });
  const url = new URL(seen[0].url);
  assert.equal(url.pathname, "/trade-api/v2/portfolio/positions");
  assert.equal(url.searchParams.get("limit"), "5");
  assert.equal(url.searchParams.get("cursor"), null);
  const headers = seen[0].init.headers;
  assert.equal(headers["KALSHI-ACCESS-KEY"], "key-id");
  const message = `${headers["KALSHI-ACCESS-TIMESTAMP"]}GET${url.pathname}`;
  const ok = crypto.verify(
    "sha256",
    Buffer.from(message),
    {
      key: crypto.createPublicKey(client.loadKey()),
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
    },
    Buffer.from(headers["KALSHI-ACCESS-SIGNATURE"], "base64"),
  );
  assert.equal(ok, true);
  assert.equal(JSON.stringify(seen).includes("BEGIN"), false);
});

test("public market scans skip auth when no key is configured", async () => {
  let auth = false;
  const client = new KalshiClient({
    baseUrl: "https://external-api.kalshi.com/trade-api/v2",
    fetchImpl: async (_url, init) => {
      auth = Boolean(init.headers["KALSHI-ACCESS-KEY"]);
      return new Response(JSON.stringify({ markets: [], cursor: "" }), { status: 200 });
    },
  });
  await client.getMarkets({ status: "open", limit: 1 });
  assert.equal(auth, false);
});

test("missing credentials fail before a portfolio call", async () => {
  let called = false;
  const client = new KalshiClient({
    baseUrl: "https://external-api.kalshi.com/trade-api/v2",
    fetchImpl: async () => {
      called = true;
      return new Response("{}", { status: 200 });
    },
  });
  await assert.rejects(() => client.getBalance(), /KALSHI_API_KEY_ID/);
  assert.equal(called, false);
});
