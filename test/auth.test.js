import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";
import { signRequest } from "../src/auth.js";

test("RSA-PSS signature covers method and path and drops the query", () => {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const timestamp = "1700000000000";
  const signature = signRequest({
    privateKey,
    timestamp,
    method: "get",
    path: "/trade-api/v2/portfolio/balance?limit=5",
  });
  const ok = crypto.verify(
    "sha256",
    Buffer.from(`${timestamp}GET/trade-api/v2/portfolio/balance`),
    {
      key: publicKey,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
    },
    Buffer.from(signature, "base64"),
  );
  assert.equal(ok, true);
  const wrong = crypto.verify(
    "sha256",
    Buffer.from(`${timestamp}GET/trade-api/v2/portfolio/balance?limit=5`),
    {
      key: publicKey,
      padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
      saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
    },
    Buffer.from(signature, "base64"),
  );
  assert.equal(wrong, false);
});

test("Ed25519 keys sign the same message", () => {
  const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
  const timestamp = "1700000000001";
  const signature = signRequest({
    privateKey,
    timestamp,
    method: "POST",
    path: "/trade-api/v2/portfolio/events/orders",
  });
  const ok = crypto.verify(
    null,
    Buffer.from(`${timestamp}POST/trade-api/v2/portfolio/events/orders`),
    publicKey,
    Buffer.from(signature, "base64"),
  );
  assert.equal(ok, true);
});
