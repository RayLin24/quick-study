import { writeFileSync } from "node:fs";

// The analyzer must never execute this file. If it does, the marker appears on disk
// and tests/safety.test.ts fails.
const marker = process.env.TS_ANALYZER_SIDE_EFFECT_MARKER;
if (marker) {
  writeFileSync(marker, "the analyzer executed repository code");
}

process.exit(3);

export const value = 1;
