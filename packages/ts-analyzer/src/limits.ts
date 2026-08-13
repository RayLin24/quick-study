import { AnalyzerError } from "./errors.ts";
import type { Limits } from "./types.ts";

export const DEFAULT_LIMITS: Limits = {
  maxFiles: 2000,
  maxFileBytes: 512 * 1024,
  maxTotalBytes: 48 * 1024 * 1024,
  maxDirectoryDepth: 24,
  timeBudgetMs: 60_000,
};

/** Absolute ceilings: a caller may lower a limit, never raise it past these values. */
export const LIMIT_CEILINGS: Limits = {
  maxFiles: 20_000,
  maxFileBytes: 4 * 1024 * 1024,
  maxTotalBytes: 256 * 1024 * 1024,
  maxDirectoryDepth: 64,
  timeBudgetMs: 600_000,
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
      throw new AnalyzerError("invalid-limit", `${key} must be a non-negative integer, got ${value}`);
    }
    const ceiling = LIMIT_CEILINGS[key];
    if (value > ceiling) {
      throw new AnalyzerError(
        "invalid-limit",
        `${key} must not exceed the hard ceiling of ${ceiling}, got ${value}`,
      );
    }
    resolved[key] = value;
  }

  return resolved;
}

export interface Deadline {
  expired(): boolean;
  elapsedMs(): number;
}

export function createDeadline(budgetMs: number): Deadline {
  const startedAt = performance.now();
  return {
    expired: () => performance.now() - startedAt >= budgetMs,
    elapsedMs: () => performance.now() - startedAt,
  };
}
