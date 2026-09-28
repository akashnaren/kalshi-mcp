import assert from "node:assert/strict";
import crypto from "node:crypto";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { apiBase, loadAuth, signRequest, signedHeaders } from "../src/auth.js";
import { requestTarget } from "../src/client.js";
import { presentBalance } from "../src/portfolio.js";
import { assertReadOnly } from "../src/safety.js";

function keyPair() {
  return crypto.generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
  });
}

test("signRequest is RSA-PSS SHA-256 over timestamp + METHOD + path", () => {
  const { privateKey, publicKey } = keyPair();
  const timestamp = "1703123456789";
  const signPath = "/trade-api/v2/portfolio/balance";
  const signature = signRequest(privateKey, timestamp, "get", `${signPath}?limit=5`);
  const message = Buffer.from(`${timestamp}GET${signPath}`, "utf8");

  assert.equal(
    crypto.verify(
      "sha256",
      message,
      {
        key: publicKey,
        padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
        saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
      },
      Buffer.from(signature, "base64"),
    ),
    true,
  );

  assert.equal(
    crypto.verify(
      "sha256",
      Buffer.from(`${timestamp}GET${signPath}?limit=5`, "utf8"),
      {
        key: publicKey,
        padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
        saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
      },
      Buffer.from(signature, "base64"),
    ),
    false,
  );
});

test("signed headers carry the key id and omit the private key", () => {
  const { privateKey } = keyPair();
  const headers = signedHeaders(
    { keyId: "key-123", privateKeyPem: privateKey },
    "GET",
    "/trade-api/v2/portfolio/balance",
    1703123456789,
  );
  assert.equal(headers["KALSHI-ACCESS-KEY"], "key-123");
  assert.equal(headers["KALSHI-ACCESS-TIMESTAMP"], "1703123456789");
  assert.match(headers["KALSHI-ACCESS-SIGNATURE"] ?? "", /^[A-Za-z0-9+/]+=*$/);
  assert.equal(JSON.stringify(headers).includes("PRIVATE KEY"), false);
  assert.equal(JSON.stringify(headers).includes(privateKey), false);
});

test("request target keeps the /trade-api/v2 prefix and drops the query from the signed path", () => {
  const target = requestTarget("https://api.elections.kalshi.com/trade-api/v2", "/portfolio/positions", {
    limit: 50,
    ticker: "KXTEST",
    cursor: undefined,
  });
  assert.equal(target.signPath, "/trade-api/v2/portfolio/positions");
  const url = new URL(target.url);
  assert.equal(url.searchParams.get("limit"), "50");
  assert.equal(url.searchParams.get("ticker"), "KXTEST");
  assert.equal(url.searchParams.has("cursor"), false);
});

test("loadAuth reads PEM from the environment and does not echo it on failure", () => {
  const { privateKey } = keyPair();
  const escaped = privateKey.replace(/\n/g, "\\n");
  const auth = loadAuth({
    KALSHI_API_KEY_ID: " key-123 ",
    KALSHI_PRIVATE_KEY_PEM: escaped,
  });
  assert.equal(auth.keyId, "key-123");
  assert.equal(auth.privateKeyPem, privateKey);

  const dir = mkdtempSync(path.join(tmpdir(), "kalshi-mcp-"));
  const pemPath = path.join(dir, "private.pem");
  writeFileSync(pemPath, privateKey);
  const fromPath = loadAuth({
    KALSHI_API_KEY_ID: "key-456",
    KALSHI_PRIVATE_KEY_PATH: pemPath,
  });
  assert.equal(fromPath.privateKeyPem, privateKey);

  assert.throws(() => loadAuth({ KALSHI_PRIVATE_KEY_PEM: privateKey }), /KALSHI_API_KEY_ID/);
  assert.throws(
    () => loadAuth({ KALSHI_API_KEY_ID: "key", KALSHI_PRIVATE_KEY_PATH: path.join(dir, "missing.pem") }),
    (err: unknown) => {
      assert.ok(err instanceof Error);
      assert.equal(err.message.includes("PRIVATE KEY"), false);
      assert.match(err.message, /KALSHI_PRIVATE_KEY_PATH/);
      return true;
    },
  );
});

test("api base defaults and strips a trailing slash", () => {
  assert.equal(apiBase({}), "https://api.elections.kalshi.com/trade-api/v2");
  assert.equal(
    apiBase({ KALSHI_API_BASE: "https://demo-api.kalshi.co/trade-api/v2/" }),
    "https://demo-api.kalshi.co/trade-api/v2",
  );
});

test("writes are refused", () => {
  assert.doesNotThrow(() => assertReadOnly("GET"));
  assert.throws(() => assertReadOnly("POST"), /read-only v1: refusing POST/);
  assert.throws(() => assertReadOnly("DELETE"), /refusing DELETE/);
});

test("presentBalance exposes cents and dollars", () => {
  assert.deepEqual(
    presentBalance({
      balance: 12345,
      balance_dollars: "123.4500",
      portfolio_value: 500,
      updated_ts: 10,
    }),
    {
      balance_cents: 12345,
      balance_dollars: "123.4500",
      portfolio_value_cents: 500,
      portfolio_value_dollars: "5.00",
      updated_ts: 10,
    },
  );
  assert.equal(presentBalance({ balance: 100 }).balance_dollars, "1.00");
});
