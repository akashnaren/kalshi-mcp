import crypto from "node:crypto";
import fs from "node:fs";
import { signRequest } from "./auth.js";
import { PolicyError } from "./policy.js";

const REQUEST_TIMEOUT_MS = 10_000;

export class KalshiClient {
  constructor({
    baseUrl,
    apiKeyId = "",
    privateKeyPem = "",
    privateKeyPath = "",
    fetchImpl = fetch,
  }) {
    this.baseUrl = String(baseUrl || "").replace(/\/$/, "");
    this.apiKeyId = apiKeyId;
    this.privateKeyPem = privateKeyPem;
    this.privateKeyPath = privateKeyPath;
    this.fetchImpl = fetchImpl;
    this._key = null;
  }

  hasCredentials() {
    return Boolean(this.apiKeyId && (this.privateKeyPem || this.privateKeyPath));
  }

  loadKey() {
    if (this._key) return this._key;
    if (!this.apiKeyId || (!this.privateKeyPem && !this.privateKeyPath)) {
      throw new PolicyError(
        "AUTH_CONFIG",
        "Set KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH. Do not commit the key file.",
      );
    }
    let pem = this.privateKeyPem;
    if (!pem) {
      try {
        pem = fs.readFileSync(this.privateKeyPath, "utf8");
      } catch {
        throw new PolicyError(
          "AUTH_CONFIG",
          "Could not read KALSHI_PRIVATE_KEY_PATH. Point it at a PEM file outside the repo.",
        );
      }
    }
    try {
      this._key = crypto.createPrivateKey(pem);
    } catch {
      throw new PolicyError("AUTH_CONFIG", "The Kalshi private key could not be parsed.");
    }
    return this._key;
  }

  getMarkets(query) {
    return this.request("GET", "/markets", { query, auth: this.hasCredentials() });
  }

  getBalance() {
    return this.request("GET", "/portfolio/balance", { auth: true });
  }

  getPositions(query) {
    return this.request("GET", "/portfolio/positions", { query, auth: true });
  }

  getFills(query) {
    return this.request("GET", "/portfolio/fills", { query, auth: true });
  }

  getOrders(query) {
    return this.request("GET", "/portfolio/orders", { query, auth: true });
  }

  createOrder(body) {
    return this.request("POST", "/portfolio/events/orders", { body, auth: true });
  }

  cancelOrder(orderId, query) {
    return this.request("DELETE", `/portfolio/events/orders/${encodeURIComponent(orderId)}`, { query, auth: true });
  }

  amendOrder(orderId, body) {
    return this.request("POST", `/portfolio/events/orders/${encodeURIComponent(orderId)}/amend`, { body, auth: true });
  }

  decreaseOrder(orderId, body) {
    return this.request("POST", `/portfolio/events/orders/${encodeURIComponent(orderId)}/decrease`, { body, auth: true });
  }

  async request(method, path, { query, body, auth = false } = {}) {
    const url = new URL(this.baseUrl + path);
    if (query) {
      for (const [key, value] of Object.entries(query)) {
        if (value === undefined || value === null || value === "") continue;
        url.searchParams.set(key, String(value));
      }
    }
    const headers = {
      Accept: "application/json",
      "User-Agent": "kalshi-mcp/1.0",
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth) {
      const timestamp = Date.now().toString();
      headers["KALSHI-ACCESS-KEY"] = this.apiKeyId;
      headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp;
      headers["KALSHI-ACCESS-SIGNATURE"] = signRequest({
        privateKey: this.loadKey(),
        timestamp,
        method,
        path: url.pathname,
      });
    }
    const response = await this.fetchImpl(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const text = await response.text();
    let payload = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = { raw: text.slice(0, 200) };
      }
    }
    if (!response.ok) {
      const detail = payload?.message || payload?.error?.message || payload?.error || response.statusText;
      throw new Error(`Kalshi ${method} ${path} failed (${response.status}): ${String(detail).slice(0, 200)}`);
    }
    return payload;
  }
}
