import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

export function fixturePath(name: string): string {
  return path.join(here, "fixtures", name);
}

export function packageRoot(): string {
  return path.join(here, "..");
}

/** Creates a throwaway directory tree so limit tests never depend on committed fixtures. */
export function makeTempTree(files: Record<string, string>): string {
  const root = mkdtempSync(path.join(tmpdir(), "ts-analyzer-"));
  for (const [relative, contents] of Object.entries(files)) {
    const target = path.join(root, relative);
    mkdirSync(path.dirname(target), { recursive: true });
    writeFileSync(target, contents, "utf8");
  }
  return root;
}
