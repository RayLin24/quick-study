#!/usr/bin/env node
import { readFileSync, writeFileSync } from "node:fs";
import path from "node:path";
import process from "node:process";
import { parseArgs } from "node:util";

import { DiagramRendererError } from "./errors.ts";
import { resolveLimits } from "./limits.ts";
import { renderDiagram, validateDiagram } from "./renderer.ts";
import { getResultSchema } from "./schema.ts";
import {
  EMPTY_SANITIZATION,
  SCHEMA_VERSION,
  TOOL_NAME,
  type Limits,
  type RenderResult,
} from "./types.ts";
import { mermaidVersion, packageVersion } from "./version.ts";

const EXIT_USAGE = 1;
const EXIT_REJECTED = 2;
const EXIT_INTERNAL = 3;

const HELP = `quick-study-diagram-renderer - validate and render Mermaid diagrams to safe SVG

Usage:
  quick-study-diagram-renderer --input <file|-> [options]

Reads one Mermaid source, validates it with mermaid.parse(), renders it with a pinned Mermaid
release at securityLevel "strict", sanitizes the SVG and prints a JSON result document.

Options:
  --input <file>          Mermaid source file, or "-" for stdin (default: "-")
  --out <file>            Write the JSON result to a file instead of stdout
  --svg-out <file>        Write the sanitized SVG to a file. Only written when the diagram passes.
  --id <name>             Deterministic id for the SVG root and its scoped CSS (default: "diagram")
  --validate-only         Run mermaid.parse() only; the result carries svg: null
  --pretty                Indent the JSON output
  --max-input-bytes <n>   Reject sources larger than this
  --max-input-lines <n>   Reject sources with more lines than this
  --max-output-bytes <n>  Reject SVG output larger than this
  --render-timeout-ms <n> Budget for the render stage
  --print-schema          Print the JSON Schema of the result and exit
  --version               Print the renderer version and exit
  --help                  Print this message and exit

Exit codes:
  0  the diagram passed; stdout holds the result document with ok: true
  1  usage or I/O error; stderr holds {"error":{"code":"usage",...}} and stdout is empty
  2  the diagram was rejected; stdout still holds a valid result document with ok: false
  3  internal error
`;

async function readStdin(): Promise<string> {
  const chunks: Buffer[] = [];
  for await (const chunk of process.stdin) {
    chunks.push(Buffer.from(chunk));
  }
  return Buffer.concat(chunks).toString("utf8");
}

function fail(code: string, message: string): never {
  process.stderr.write(`${JSON.stringify({ error: { code, message } })}\n`);
  process.exit(code === "usage" ? EXIT_USAGE : EXIT_INTERNAL);
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
      allowPositionals: false,
      strict: true,
      options: {
        input: { type: "string" },
        out: { type: "string" },
        "svg-out": { type: "string" },
        id: { type: "string" },
        "validate-only": { type: "boolean" },
        pretty: { type: "boolean" },
        "max-input-bytes": { type: "string" },
        "max-input-lines": { type: "string" },
        "max-output-bytes": { type: "string" },
        "render-timeout-ms": { type: "string" },
        "print-schema": { type: "boolean" },
        version: { type: "boolean" },
        help: { type: "boolean" },
      },
    });
  } catch (error) {
    fail("usage", (error as Error).message);
  }

  const { values } = parsed;

  if (values.help) {
    process.stdout.write(HELP);
    return;
  }
  if (values.version) {
    process.stdout.write(`${packageVersion()}\n`);
    return;
  }
  if (values["print-schema"]) {
    process.stdout.write(`${JSON.stringify(getResultSchema(), null, 2)}\n`);
    return;
  }

  const input = values.input ?? "-";
  let source: string;
  try {
    source = input === "-" ? await readStdin() : readFileSync(path.resolve(input), "utf8");
  } catch (error) {
    fail("usage", `Could not read --input "${input}": ${(error as Error).message}`);
  }

  const limitOverrides: Partial<Limits> = {};
  const maxInputBytes = parseInteger("--max-input-bytes", values["max-input-bytes"]);
  const maxInputLines = parseInteger("--max-input-lines", values["max-input-lines"]);
  const maxOutputBytes = parseInteger("--max-output-bytes", values["max-output-bytes"]);
  const renderTimeoutMs = parseInteger("--render-timeout-ms", values["render-timeout-ms"]);
  if (maxInputBytes !== undefined) limitOverrides.maxInputBytes = maxInputBytes;
  if (maxInputLines !== undefined) limitOverrides.maxInputLines = maxInputLines;
  if (maxOutputBytes !== undefined) limitOverrides.maxOutputBytes = maxOutputBytes;
  if (renderTimeoutMs !== undefined) limitOverrides.renderTimeoutMs = renderTimeoutMs;

  const options = { limits: limitOverrides, ...(values.id === undefined ? {} : { id: values.id }) };

  let result: RenderResult;
  try {
    if (values["validate-only"]) {
      const startedAt = performance.now();
      const validation = await validateDiagram(source, options);
      // The document shape stays identical to a full render so callers parse one contract.
      result = {
        schemaVersion: SCHEMA_VERSION,
        tool: { name: TOOL_NAME, version: packageVersion(), mermaid: mermaidVersion() },
        ok: validation.ok,
        diagramType: validation.diagramType,
        svg: null,
        error: validation.error,
        sanitization: EMPTY_SANITIZATION,
        stats: {
          inputBytes: Buffer.byteLength(source, "utf8"),
          inputLines: source.length === 0 ? 0 : source.split(/\r?\n/).length,
          outputBytes: 0,
          durationMs: Math.round(performance.now() - startedAt),
        },
        limits: resolveLimits(limitOverrides),
      };
    } else {
      result = await renderDiagram(source, options);
    }
  } catch (error) {
    if (error instanceof DiagramRendererError) {
      fail("usage", error.message);
    }
    throw error;
  }

  const document = `${JSON.stringify(result, null, values.pretty ? 2 : undefined)}\n`;
  const outFile = values.out;
  try {
    if (outFile !== undefined) {
      writeFileSync(path.resolve(outFile), document, "utf8");
    } else {
      process.stdout.write(document);
    }
    const svgOut = values["svg-out"];
    if (svgOut !== undefined && result.ok && result.svg !== null) {
      writeFileSync(path.resolve(svgOut), result.svg, "utf8");
    }
  } catch (error) {
    fail("usage", `Could not write output: ${(error as Error).message}`);
  }

  if (!result.ok) {
    process.exit(EXIT_REJECTED);
  }
}

main().catch((error: unknown) => {
  process.stderr.write(
    `${JSON.stringify({
      error: { code: "internal", message: error instanceof Error ? error.message : String(error) },
    })}\n`,
  );
  process.exit(EXIT_INTERNAL);
});
