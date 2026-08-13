import { readFileSync } from "node:fs";
import { createRequire } from "node:module";

interface PackageManifest {
  version?: string;
  dependencies?: Record<string, string>;
}

let manifest: PackageManifest | null = null;

function ownManifest(): PackageManifest {
  manifest ??= JSON.parse(
    readFileSync(new URL("../package.json", import.meta.url), "utf8"),
  ) as PackageManifest;
  return manifest;
}

export function packageVersion(): string {
  return ownManifest().version ?? "0.0.0";
}

/**
 * Version of the Mermaid release actually loaded. The dependency is pinned exactly, so the
 * manifest is a correct fallback when Mermaid's export map hides its own package.json.
 */
export function mermaidVersion(): string {
  try {
    const required = createRequire(import.meta.url)("mermaid/package.json") as PackageManifest;
    if (required.version) {
      return required.version;
    }
  } catch {
    // Fall through to the pinned dependency range.
  }
  return ownManifest().dependencies?.["mermaid"] ?? "unknown";
}
