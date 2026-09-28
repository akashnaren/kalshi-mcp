import crypto from "node:crypto";
import { readFileSync } from "node:fs";

export const DEFAULT_API_BASE = "https://api.elections.kalshi.com/trade-api/v2";

export type AuthMaterial = {
  keyId: string;
  privateKeyPem: string;
};

function normalizePem(pem: string): string {
  const trimmed = pem.trim();
  if (!trimmed.includes("\n") && trimmed.includes("\\n")) {
    return trimmed.replace(/\\n/g, "\n");
  }
  return trimmed;
}

export function loadAuth(env: NodeJS.ProcessEnv = process.env): AuthMaterial {
  const keyId = env.KALSHI_API_KEY_ID?.trim();
  if (!keyId) {
    throw new Error("KALSHI_API_KEY_ID is required");
  }

  const inline = env.KALSHI_PRIVATE_KEY_PEM;
  let privateKeyPem: string | undefined;
  if (inline?.trim()) {
    privateKeyPem = normalizePem(inline);
  } else if (env.KALSHI_PRIVATE_KEY_PATH?.trim()) {
    try {
      privateKeyPem = readFileSync(env.KALSHI_PRIVATE_KEY_PATH.trim(), "utf8");
    } catch (err) {
      const code = err && typeof err === "object" && "code" in err ? String(err.code) : "";
      throw new Error(
        code
          ? `unable to read KALSHI_PRIVATE_KEY_PATH (${code})`
          : "unable to read KALSHI_PRIVATE_KEY_PATH",
      );
    }
  }

  if (!privateKeyPem?.includes("PRIVATE KEY")) {
    throw new Error("Set KALSHI_PRIVATE_KEY_PEM or KALSHI_PRIVATE_KEY_PATH");
  }

  return { keyId, privateKeyPem };
}

export function apiBase(env: NodeJS.ProcessEnv = process.env): string {
  const base = env.KALSHI_API_BASE?.trim() || DEFAULT_API_BASE;
  return base.replace(/\/+$/, "");
}

/**
 * Sign `${timestampMs}${METHOD}${path}` with RSA-PSS SHA-256.
 * Salt length is the digest length. Query strings are not part of the message.
 */
export function signRequest(
  privateKeyPem: string,
  timestampMs: string,
  method: string,
  path: string,
): string {
  const pathWithoutQuery = path.split("?")[0] ?? path;
  const message = `${timestampMs}${method.toUpperCase()}${pathWithoutQuery}`;
  const signature = crypto.sign("sha256", Buffer.from(message, "utf8"), {
    key: privateKeyPem,
    padding: crypto.constants.RSA_PKCS1_PSS_PADDING,
    saltLength: crypto.constants.RSA_PSS_SALTLEN_DIGEST,
  });
  return signature.toString("base64");
}

export function signedHeaders(
  auth: AuthMaterial,
  method: string,
  signPath: string,
  nowMs: number = Date.now(),
): Record<string, string> {
  const timestamp = String(nowMs);
  return {
    "KALSHI-ACCESS-KEY": auth.keyId,
    "KALSHI-ACCESS-TIMESTAMP": timestamp,
    "KALSHI-ACCESS-SIGNATURE": signRequest(auth.privateKeyPem, timestamp, method, signPath),
    "User-Agent": "tinkabot-kalshi-mcp/0.1",
    Accept: "application/json",
  };
}
