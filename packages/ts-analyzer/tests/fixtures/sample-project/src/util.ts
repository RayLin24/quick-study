/** Normalizes a raw label into a comparable slug. */
export function slugify(value: string): string {
  return value.trim().toLowerCase();
}

export function titleCase(value: string): string {
  return slugify(value).toUpperCase();
}

export const DEFAULT_LOCALE = "en";

export interface Formatter {
  format(value: string): string;
}

export type Locale = "en" | "zh";

export enum Level {
  Low = 1,
  High = 2,
}

function internalOnly(): number {
  return 42;
}

export const useInternal = () => internalOnly();
