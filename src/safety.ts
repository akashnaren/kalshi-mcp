/**
 * v1 is read-only. Writes stay refused.
 * Flipping ALLOW_WRITES does not add trading tools; those are not implemented.
 */
export const ALLOW_WRITES = false;

export function assertReadOnly(method: string): void {
  const normalized = method.toUpperCase();
  if (normalized === "GET") return;
  if (ALLOW_WRITES) {
    throw new Error(`${normalized} is not implemented`);
  }
  throw new Error(`read-only v1: refusing ${normalized}`);
}
