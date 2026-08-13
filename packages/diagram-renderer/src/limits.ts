import { DiagramRendererError } from "./errors.ts";
import type { Limits } from "./types.ts";

export const DEFAULT_LIMITS: Limits = {
  maxInputBytes: 64 * 1024,
  maxInputLines: 2000,
  maxOutputBytes: 4 * 1024 * 1024,
  renderTimeoutMs: 15_000,
};

/** Absolute ceilings: a caller may lower a limit, never raise it past these values. */
export const LIMIT_CEILINGS: Limits = {
  maxInputBytes: 1024 * 1024,
  maxInputLines: 20_000,
  maxOutputBytes: 32 * 1024 * 1024,
  renderTimeoutMs: 120_000,
};

const LIMIT_KEYS = Object.keys(DEFAULT_LIMITS) as (keyof Limits)[];

export function resolveLimits(overrides?: Partial<Limits>): Limits {
  const resolved: Limits = { ...DEFAULT_LIMITS };
  if (!overrides) {
    return resolved;
  }

  for (const key of LIMIT_KEYS) {
    const value = overrides[key];
    if (value === undefined) {
      continue;
    }
    if (!Number.isInteger(value) || value < 0) {
      throw new DiagramRendererError(
        "invalid-limit",
        `${key} must be a non-negative integer, got ${value}`,
      );
    }
    const ceiling = LIMIT_CEILINGS[key];
    if (value > ceiling) {
      throw new DiagramRendererError(
        "invalid-limit",
        `${key} must not exceed the hard ceiling of ${ceiling}, got ${value}`,
      );
    }
    resolved[key] = value;
  }

  return resolved;
}
