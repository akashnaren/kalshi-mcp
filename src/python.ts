import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

export function repoRoot(): string {
  const here = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(here, "..");
}

export interface WorkerResponse {
  id?: number;
  ok?: boolean;
  error?: string;
  tools?: unknown;
  result?: unknown;
}

interface Pending {
  resolve: (value: WorkerResponse) => void;
  reject: (error: Error) => void;
  timer: NodeJS.Timeout;
}

/** One Python process for the life of the Node server. Keeps the market cache warm. */
export class PythonBridge {
  private readonly child: ChildProcessWithoutNullStreams;
  private buffer = "";
  private nextId = 1;
  private readonly pending = new Map<number, Pending>();

  constructor(root: string, python = process.env.KALSHI_PYTHON || "python3") {
    this.child = spawn(python, ["-m", "kalshi_readonly.dispatch"], {
      cwd: root,
      env: { ...process.env, PYTHONPATH: root, PYTHONUNBUFFERED: "1" },
      stdio: ["pipe", "pipe", "pipe"],
    });
    this.child.unref();
    for (const stream of [this.child.stdin, this.child.stdout, this.child.stderr]) {
      const maybe = stream as { unref?: () => void };
      maybe.unref?.();
    }
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk: string) => this.onStdout(chunk));
    this.child.stderr.on("data", (chunk: Buffer | string) => {
      process.stderr.write(chunk);
    });
    this.child.on("error", (error) => {
      this.failAll(error.message);
    });
    this.child.on("exit", (code, signal) => {
      const message = `python worker exited (${signal ?? code ?? "unknown"})`;
      this.failAll(message);
      if (code !== 0 && code !== null) {
        console.error(message);
        process.exit(code);
      }
    });
  }

  request(body: Record<string, unknown>, timeoutMs = 70_000): Promise<WorkerResponse> {
    if (!this.child.stdin.writable) {
      return Promise.reject(new Error("python worker is not running"));
    }
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("python worker timed out"));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(`${JSON.stringify({ id, ...body })}\n`);
    });
  }

  private onStdout(chunk: string): void {
    this.buffer += chunk;
    for (;;) {
      const newline = this.buffer.indexOf("\n");
      if (newline < 0) {
        return;
      }
      const line = this.buffer.slice(0, newline).trim();
      this.buffer = this.buffer.slice(newline + 1);
      if (!line) {
        continue;
      }
      let message: WorkerResponse;
      try {
        message = JSON.parse(line) as WorkerResponse;
      } catch {
        this.failAll("python worker sent non-json");
        return;
      }
      if (typeof message.id !== "number") {
        continue;
      }
      const pending = this.pending.get(message.id);
      if (!pending) {
        continue;
      }
      clearTimeout(pending.timer);
      this.pending.delete(message.id);
      if (message.ok) {
        pending.resolve(message);
      } else {
        pending.reject(new Error(message.error || "kalshi request failed"));
      }
    }
  }

  private failAll(message: string): void {
    for (const [id, pending] of this.pending) {
      clearTimeout(pending.timer);
      pending.reject(new Error(message));
      this.pending.delete(id);
    }
  }
}
