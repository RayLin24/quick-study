import { createHash } from "node:crypto";
import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";

import { AnalyzerError } from "./errors.ts";
import type { Deadline } from "./limits.ts";
import type { AnalyzerDiagnostic, Language, Limits } from "./types.ts";

const LANGUAGE_BY_EXTENSION = new Map<string, Language>([
  [".ts", "ts"],
  [".tsx", "tsx"],
  [".mts", "mts"],
  [".cts", "cts"],
  [".js", "js"],
  [".jsx", "jsx"],
  [".mjs", "mjs"],
  [".cjs", "cjs"],
]);

/**
 * Directories that never contain first-party sources. Skipping them keeps the analysis
 * bounded and avoids walking dependency trees of an untrusted repository.
 */
const EXCLUDED_DIRECTORIES = new Set([
  ".cache",
  ".git",
  ".hg",
  ".idea",
  ".next",
  ".nuxt",
  ".output",
  ".parcel-cache",
  ".pnpm-store",
  ".svn",
  ".turbo",
  ".venv",
  ".vscode",
  ".yarn",
  "__pycache__",
  "bower_components",
  "build",
  "coverage",
  "dist",
  "node_modules",
  "out",
  "target",
  "vendor",
  "venv",
]);

export interface DiscoveredFile {
  absolutePath: string;
  /** POSIX path relative to the analysis root. */
  relativePath: string;
  language: Language;
  text: string;
  bytes: number;
  lines: number;
  sha256: string;
}

export interface DiscoveryResult {
  files: DiscoveredFile[];
  diagnostics: AnalyzerDiagnostic[];
  truncationReasons: string[];
}

export function toPosix(value: string): string {
  return value.split(path.sep).join("/");
}

function isAnalyzableFile(name: string): boolean {
  if (name.endsWith(".d.ts") || name.endsWith(".d.mts") || name.endsWith(".d.cts")) {
    return false;
  }
  if (name.endsWith(".min.js") || name.endsWith(".min.mjs") || name.endsWith(".min.cjs")) {
    return false;
  }
  return LANGUAGE_BY_EXTENSION.has(path.extname(name).toLowerCase());
}

function countLines(text: string): number {
  if (text.length === 0) {
    return 0;
  }
  let lines = 1;
  for (let index = 0; index < text.length; index += 1) {
    if (text.charCodeAt(index) === 10) {
      lines += 1;
    }
  }
  return text.endsWith("\n") ? lines - 1 : lines;
}

/** Rejects entries that would take the analysis outside the approved root. */
export function resolveEntry(root: string, entry: string): string {
  const absolute = path.resolve(root, entry);
  const relative = path.relative(root, absolute);
  if (relative.startsWith("..") || path.isAbsolute(relative)) {
    throw new AnalyzerError(
      "entry-outside-root",
      `Entry "${entry}" resolves outside the analysis root ${root}`,
    );
  }
  return absolute;
}

interface Budget {
  fileCount: number;
  totalBytes: number;
  stopped: boolean;
}

export function discoverSources(
  root: string,
  entries: string[],
  limits: Limits,
  deadline: Deadline,
): DiscoveryResult {
  const diagnostics: AnalyzerDiagnostic[] = [];
  const truncationReasons = new Set<string>();
  const files = new Map<string, DiscoveredFile>();
  const budget: Budget = { fileCount: 0, totalBytes: 0, stopped: false };

  const stop = (reason: string, message: string, target: string | null): void => {
    budget.stopped = true;
    if (!truncationReasons.has(reason)) {
      truncationReasons.add(reason);
      diagnostics.push({ severity: "warning", code: reason, message, path: target });
    }
  };

  const addFile = (absolutePath: string): void => {
    if (budget.stopped) {
      return;
    }
    const relativePath = toPosix(path.relative(root, absolutePath));
    if (files.has(relativePath)) {
      return;
    }
    if (deadline.expired()) {
      stop("limit.time-budget", "Stopped collecting sources: the time budget expired", null);
      return;
    }
    if (budget.fileCount >= limits.maxFiles) {
      stop(
        "limit.max-files",
        `Stopped collecting sources after ${limits.maxFiles} files`,
        relativePath,
      );
      return;
    }

    let size: number;
    try {
      size = statSync(absolutePath).size;
    } catch (error) {
      diagnostics.push({
        severity: "warning",
        code: "io.stat-failed",
        message: `Could not stat file: ${(error as Error).message}`,
        path: relativePath,
      });
      return;
    }

    if (size > limits.maxFileBytes) {
      truncationReasons.add("limit.file-too-large");
      diagnostics.push({
        severity: "warning",
        code: "limit.file-too-large",
        message: `Skipped file of ${size} bytes, above the ${limits.maxFileBytes} byte limit`,
        path: relativePath,
      });
      return;
    }
    if (budget.totalBytes + size > limits.maxTotalBytes) {
      stop(
        "limit.total-bytes",
        `Stopped collecting sources at ${budget.totalBytes} bytes, the budget is ${limits.maxTotalBytes}`,
        relativePath,
      );
      return;
    }

    let text: string;
    try {
      text = readFileSync(absolutePath, "utf8");
    } catch (error) {
      diagnostics.push({
        severity: "warning",
        code: "io.read-failed",
        message: `Could not read file: ${(error as Error).message}`,
        path: relativePath,
      });
      return;
    }

    const language = LANGUAGE_BY_EXTENSION.get(path.extname(absolutePath).toLowerCase());
    if (!language) {
      return;
    }

    budget.fileCount += 1;
    budget.totalBytes += size;
    files.set(relativePath, {
      absolutePath,
      relativePath,
      language,
      text,
      bytes: size,
      lines: countLines(text),
      sha256: createHash("sha256").update(text, "utf8").digest("hex"),
    });
  };

  const walk = (directory: string, depth: number): void => {
    if (budget.stopped) {
      return;
    }
    if (depth > limits.maxDirectoryDepth) {
      truncationReasons.add("limit.max-depth");
      diagnostics.push({
        severity: "warning",
        code: "limit.max-depth",
        message: `Stopped descending below depth ${limits.maxDirectoryDepth}`,
        path: toPosix(path.relative(root, directory)),
      });
      return;
    }

    let dirents;
    try {
      dirents = readdirSync(directory, { withFileTypes: true });
    } catch (error) {
      diagnostics.push({
        severity: "warning",
        code: "io.readdir-failed",
        message: `Could not list directory: ${(error as Error).message}`,
        path: toPosix(path.relative(root, directory)),
      });
      return;
    }

    // Sorting keeps discovery, and therefore truncation, reproducible across platforms.
    dirents.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

    for (const dirent of dirents) {
      if (budget.stopped) {
        return;
      }
      const child = path.join(directory, dirent.name);
      if (dirent.isSymbolicLink()) {
        diagnostics.push({
          severity: "info",
          code: "safety.symlink-skipped",
          message: "Symbolic links are never followed",
          path: toPosix(path.relative(root, child)),
        });
        continue;
      }
      if (dirent.isDirectory()) {
        if (EXCLUDED_DIRECTORIES.has(dirent.name)) {
          continue;
        }
        walk(child, depth + 1);
        continue;
      }
      if (dirent.isFile() && isAnalyzableFile(dirent.name)) {
        addFile(child);
      }
    }
  };

  for (const entry of entries) {
    if (budget.stopped) {
      break;
    }
    const absolute = resolveEntry(root, entry);
    let stats;
    try {
      stats = statSync(absolute);
    } catch (error) {
      throw new AnalyzerError(
        "entry-not-found",
        `Entry "${entry}" could not be read: ${(error as Error).message}`,
      );
    }
    if (stats.isDirectory()) {
      walk(absolute, 0);
    } else if (stats.isFile()) {
      if (!isAnalyzableFile(path.basename(absolute))) {
        diagnostics.push({
          severity: "info",
          code: "discovery.unsupported-file",
          message: "Entry is not a JavaScript or TypeScript source file",
          path: toPosix(path.relative(root, absolute)),
        });
        continue;
      }
      addFile(absolute);
    }
  }

  if (deadline.expired()) {
    if (!truncationReasons.has("limit.time-budget")) {
      truncationReasons.add("limit.time-budget");
      diagnostics.push({
        severity: "warning",
        code: "limit.time-budget",
        message: "The analysis time budget expired",
        path: null,
      });
    }
  }

  return {
    files: [...files.values()].sort((a, b) => a.relativePath.localeCompare(b.relativePath)),
    diagnostics,
    truncationReasons: [...truncationReasons].sort(),
  };
}
