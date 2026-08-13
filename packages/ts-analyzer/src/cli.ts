#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { parseArgs } from "node:util";

import { analyzeProject } from "./analyze.ts";
import { AnalyzerError } from "./errors.ts";
import { getOutputSchema } from "./schema.ts";
import type { Limits } from "./types.ts";
import { packageVersion } from "./version.ts";

const EXIT_USAGE = 1;
const EXIT_LIMIT = 2;
const EXIT_INTERNAL = 3;

const HELP = `quick-study-ts-analyzer - static JavaScript/TypeScript repository analysis

Usage:
  quick-study-ts-analyzer [options] [path...]

Paths are resolved against --root and must stay inside it. With no path the whole root
is analyzed. The result is a single JSON document; see --print-schema for its contract.

Options:
  --root <dir>              Analysis root (default: current directory)
  --dir <dir>               Add a directory to analyze (repeatable)
  --file <file>             Add a single file to analyze (repeatable)
  --files-from <file>       Read newline-delimited paths from a file, or "-" for stdin
  --out <file>              Write the JSON document to a file instead of stdout
  --pretty                  Indent the JSON output
  --max-files <n>           Maximum number of source files
  --max-file-bytes <n>      Skip files larger than this
  --max-total-bytes <n>     Maximum total bytes of source to read
  --max-directory-depth <n> Maximum directory recursion depth
  --time-budget-ms <n>      Wall-clock budget for the whole analysis
  --strict-limits           Fail with exit code 2 instead of returning a truncated result
  --print-schema            Print the JSON Schema of the output and exit
  --version                 Print the analyzer version and exit
  --help                    Print this message and exit

Exit codes:
  0  analysis completed (possibly truncated; check limits.truncated)
  1  usage or I/O error
  2  a limit was exceeded while --strict-limits was set
  3  internal error
`;

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function fail(code: string, message: string, extra: Record<string, unknown> = {}): never {
  process.stderr.write(`${JSON.stringify({ error: { code, message, ...extra } })}\n`);
  process.exit(code === "limit-exceeded" ? EXIT_LIMIT : code === "usage" ? EXIT_USAGE : EXIT_INTERNAL);
}

function parseInteger(name: string, raw: string | undefined): number | undefined {
  if (raw === undefined) {
    return undefined;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) {
    fail("usage", `${name} must be a non-negative integer, got "${raw}"`);
  }
  return value;
}

async function main(): Promise<void> {
  let parsed;
  try {
    parsed = parseArgs({
      args: process.argv.slice(2),
      allowPositionals: true,
      strict: true,
      options: {
        root: { type: "string" },
        dir: { type: "string", multiple: true },
        file: { type: "string", multiple: true },
        "files-from": { type: "string" },
        out: { type: "string" },
        pretty: { type: "boolean" },
        "max-files": { type: "string" },
        "max-file-bytes": { type: "string" },
        "max-total-bytes": { type: "string" },
        "max-directory-depth": { type: "string" },
        "time-budget-ms": { type: "string" },
        "strict-limits": { type: "boolean" },
        "print-schema": { type: "boolean" },
        version: { type: "boolean" },
        help: { type: "boolean" },
      },
    });
  } catch (error) {
    fail("usage", (error as Error).message);
  }

  const { values, positionals } = parsed;

  if (values.help) {
    process.stdout.write(HELP);
    return;
  }
  if (values.version) {
    process.stdout.write(`${packageVersion()}\n`);
    return;
  }
  if (values["print-schema"]) {
    process.stdout.write(`${JSON.stringify(getOutputSchema(), null, 2)}\n`);
    return;
  }

  const root = path.resolve(values.root ?? process.cwd());
  const entries: string[] = [...positionals, ...(values.dir ?? []), ...(values.file ?? [])];

  const filesFrom = values["files-from"];
  if (filesFrom !== undefined) {
    let listing: string;
    try {
      listing =
        filesFrom === "-" ? await readStdin() : readFileSync(path.resolve(filesFrom), "utf8");
    } catch (error) {
      fail("usage", `Could not read --files-from "${filesFrom}": ${(error as Error).message}`);
    }
    for (const line of listing.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (trimmed.length > 0 && !trimmed.startsWith("#")) {
        entries.push(trimmed);
      }
    }
  }

  const limitOverrides: Partial<Limits> = {};
  const maxFiles = parseInteger("--max-files", values["max-files"]);
  const maxFileBytes = parseInteger("--max-file-bytes", values["max-file-bytes"]);
  const maxTotalBytes = parseInteger("--max-total-bytes", values["max-total-bytes"]);
  const maxDirectoryDepth = parseInteger("--max-directory-depth", values["max-directory-depth"]);
  const timeBudgetMs = parseInteger("--time-budget-ms", values["time-budget-ms"]);
  if (maxFiles !== undefined) limitOverrides.maxFiles = maxFiles;
  if (maxFileBytes !== undefined) limitOverrides.maxFileBytes = maxFileBytes;
  if (maxTotalBytes !== undefined) limitOverrides.maxTotalBytes = maxTotalBytes;
  if (maxDirectoryDepth !== undefined) limitOverrides.maxDirectoryDepth = maxDirectoryDepth;
  if (timeBudgetMs !== undefined) limitOverrides.timeBudgetMs = timeBudgetMs;

  let result;
  try {
    result = await analyzeProject({ root, entries, limits: limitOverrides });
  } catch (error) {
    if (error instanceof AnalyzerError) {
      fail("usage", error.message, { analyzerCode: error.code });
    }
    throw error;
  }

  if (values["strict-limits"] && result.limits.truncated) {
    fail("limit-exceeded", "The analysis hit a configured limit", {
      truncationReasons: result.limits.truncationReasons,
    });
  }

  const document = `${JSON.stringify(result, null, values.pretty ? 2 : undefined)}\n`;
  const outFile = values.out;
  if (outFile !== undefined) {
    try {
      writeFileSync(path.resolve(outFile), document, "utf8");
    } catch (error) {
      fail("usage", `Could not write --out "${outFile}": ${(error as Error).message}`);
    }
    return;
  }
  process.stdout.write(document);
}

main().catch((error: unknown) => {
  process.stderr.write(
    `${JSON.stringify({
      error: { code: "internal", message: error instanceof Error ? error.message : String(error) },
    })}\n`,
  );
  process.exit(EXIT_INTERNAL);
});
