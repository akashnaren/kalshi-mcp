import { apiBase, loadAuth, signedHeaders, type AuthMaterial } from "./auth.js";
import { assertReadOnly } from "./safety.js";

export type Query = Record<string, string | number | undefined>;

export type GetOptions = {
  base?: string;
  auth?: AuthMaterial;
  nowMs?: number;
  fetchImpl?: typeof fetch;
  timeoutMs?: number;
};

const DEFAULT_LIMIT = 50;
const MAX_LIMIT = 200;

export function clampLimit(limit: number | undefined, fallback = DEFAULT_LIMIT): number {
  if (limit === undefined) return fallback;
  if (!Number.isInteger(limit) || limit < 1 || limit > MAX_LIMIT) {
    throw new Error(`limit must be an integer from 1 to ${MAX_LIMIT}`);
  }
  return limit;
}

export function requestTarget(
  base: string,
  path: string,
  query?: Query,
): { url: string; signPath: string } {
  const trimmedBase = base.replace(/\/+$/, "");
  const rel = path.startsWith("/") ? path : `/${path}`;
  const qIndex = rel.indexOf("?");
  const pathname = qIndex === -1 ? rel : rel.slice(0, qIndex);
  const url = new URL(`${trimmedBase}${pathname}`);
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }
  return { url: url.toString(), signPath: url.pathname };
}

function errorDetail(text: string): string {
  try {
    const body = JSON.parse(text) as { message?: unknown; code?: unknown };
    const message = typeof body.message === "string" ? body.message : "";
    const code = typeof body.code === "string" ? body.code : "";
    const detail = [code, message].filter(Boolean).join(": ");
    if (detail && !/PRIVATE KEY|-----BEGIN/.test(detail)) return detail.slice(0, 300);
  } catch {
    return "";
  }
  return "";
}

const PUBLIC_HEADERS = {
  "User-Agent": "tinkabot-kalshi-mcp/0.1",
  Accept: "application/json",
};

async function sendGet(
  path: string,
  query: Query | undefined,
  opts: GetOptions,
  headers: Record<string, string>,
): Promise<unknown> {
  assertReadOnly("GET");
  const base = (opts.base ?? apiBase()).replace(/\/+$/, "");
  const { url, signPath } = requestTarget(base, path, query);
  const fetchImpl = opts.fetchImpl ?? fetch;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), opts.timeoutMs ?? 20_000);
  try {
    const res = await fetchImpl(url, {
      method: "GET",
      headers,
      signal: controller.signal,
      redirect: "error",
    });
    const text = await res.text();
    if (!res.ok) {
      const detail = errorDetail(text);
      throw new Error(
        detail
          ? `Kalshi GET ${signPath} failed: ${res.status} ${detail}`
          : `Kalshi GET ${signPath} failed: ${res.status}`,
      );
    }
    try {
      return JSON.parse(text) as unknown;
    } catch {
      throw new Error(`Kalshi GET ${signPath} returned non-JSON`);
    }
  } finally {
    clearTimeout(timeout);
  }
}

export async function kalshiGet(
  path: string,
  query?: Query,
  opts: GetOptions = {},
): Promise<unknown> {
  const base = (opts.base ?? apiBase()).replace(/\/+$/, "");
  const auth = opts.auth ?? loadAuth();
  const { signPath } = requestTarget(base, path, query);
  const headers = signedHeaders(auth, "GET", signPath, opts.nowMs);
  return sendGet(path, query, opts, headers);
}

/** Unsigned GET for public market data. No key material is attached. */
export async function kalshiPublicGet(
  path: string,
  query?: Query,
  opts: GetOptions = {},
): Promise<unknown> {
  return sendGet(path, query, opts, PUBLIC_HEADERS);
}
