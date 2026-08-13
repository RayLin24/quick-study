import { readFileSync } from "node:fs";

let cached: string | null = null;

export function packageVersion(): string {
  if (cached === null) {
    const raw = readFileSync(new URL("../package.json", import.meta.url), "utf8");
    cached = (JSON.parse(raw) as { version?: string }).version ?? "0.0.0";
  }
  return cached;
}
