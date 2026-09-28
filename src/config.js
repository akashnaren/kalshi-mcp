import { isSafeMode } from "./policy.js";

const DEFAULT_BASE_URL = "https://external-api.kalshi.com/trade-api/v2";

export function loadConfig(env = process.env) {
  const baseUrl = String(env.KALSHI_BASE_URL || DEFAULT_BASE_URL).trim().replace(/\/$/, "");
  return {
    apiKeyId: String(env.KALSHI_API_KEY_ID || "").trim(),
    privateKeyPath: String(env.KALSHI_PRIVATE_KEY_PATH || "").trim(),
    baseUrl,
    safeMode: isSafeMode(env),
  };
}
