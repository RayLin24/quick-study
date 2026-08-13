import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const packageRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const cliEntry = path.join(packageRoot, "src", "cli.ts");

const FLOWCHART = "flowchart LR\n  A[Start] --> B[End]\n";

interface CliRun {
  code: number;
  stdout: string;
  stderr: string;
}

function runCli(args: string[], stdin?: string): Promise<CliRun> {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      ["--disable-warning=ExperimentalWarning", cliEntry, ...args],
      { stdio: ["pipe", "pipe", "pipe"] },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk: string) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk: string) => {
      stderr += chunk;
    });
    child.on("error", reject);
    child.on("close", (code) => resolve({ code: code ?? -1, stdout, stderr }));
    child.stdin.end(stdin ?? "");
  });
}

function tempFile(name: string, contents = ""): string {
  const directory = mkdtempSync(path.join(tmpdir(), "diagram-renderer-cli-"));
  const target = path.join(directory, name);
  if (contents.length > 0) {
    writeFileSync(target, contents, "utf8");
  }
  return target;
}

test("prints the result JSON schema", async () => {
  const run = await runCli(["--print-schema"]);
  assert.equal(run.code, 0, run.stderr);
  const schema = JSON.parse(run.stdout) as Record<string, unknown>;
  assert.equal(typeof schema["$id"], "string");
  assert.equal(schema["type"], "object");
});

test("renders a diagram read from stdin", async () => {
  const run = await runCli(["--input", "-", "--id", "cli"], FLOWCHART);
  assert.equal(run.code, 0, run.stderr);

  const result = JSON.parse(run.stdout) as { ok: boolean; svg: string; diagramType: string };
  assert.equal(result.ok, true);
  assert.equal(result.diagramType, "flowchart-v2");
  assert.match(result.svg, /^<svg /);
});

test("renders a diagram read from a file and writes the SVG out", async () => {
  const input = tempFile("diagram.mmd", FLOWCHART);
  const svgOut = path.join(path.dirname(input), "diagram.svg");

  const run = await runCli(["--input", input, "--svg-out", svgOut, "--id", "file"]);
  assert.equal(run.code, 0, run.stderr);
  assert.match(readFileSync(svgOut, "utf8"), /^<svg /);
});

test("writes the result document to --out", async () => {
  const outFile = tempFile("result.json");
  const run = await runCli(["--input", "-", "--out", outFile], FLOWCHART);
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.stdout.trim(), "");

  const result = JSON.parse(readFileSync(outFile, "utf8")) as { ok: boolean };
  assert.equal(result.ok, true);
});

test("validates without rendering when --validate-only is set", async () => {
  const run = await runCli(["--input", "-", "--validate-only"], FLOWCHART);
  assert.equal(run.code, 0, run.stderr);

  const result = JSON.parse(run.stdout) as { ok: boolean; svg: string | null; diagramType: string };
  assert.equal(result.ok, true);
  assert.equal(result.svg, null);
  assert.equal(result.diagramType, "flowchart-v2");
});

test("exits with code 2 and a parseable result when the diagram is rejected", async () => {
  const run = await runCli(["--input", "-"], "flowchart LR\n  A --> ((((\n");
  assert.equal(run.code, 2);

  const result = JSON.parse(run.stdout) as {
    ok: boolean;
    svg: string | null;
    error: { stage: string; code: string; line: number | null };
  };
  assert.equal(result.ok, false);
  assert.equal(result.svg, null);
  assert.equal(result.error.stage, "parse");
  assert.equal(result.error.code, "parse.syntax-error");
  assert.equal(result.error.line, 2);
});

test("never writes an SVG file for a rejected diagram", async () => {
  const svgOut = tempFile("never.svg");
  const run = await runCli(["--input", "-", "--svg-out", svgOut], "flowchart LR\n  A --> ((((\n");
  assert.equal(run.code, 2);
  assert.equal(existsSync(svgOut), false);
});

test("emits machine-readable usage errors on stderr with exit code 1", async () => {
  const run = await runCli(["--definitely-not-an-option"]);
  assert.equal(run.code, 1);
  assert.equal(run.stdout, "");

  const error = JSON.parse(run.stderr) as { error: { code: string; message: string } };
  assert.equal(error.error.code, "usage");
  assert.match(error.error.message, /--definitely-not-an-option/);
});

test("treats an unreadable input file as a usage error", async () => {
  const run = await runCli(["--input", path.join(tmpdir(), "does-not-exist-9f3a.mmd")]);
  assert.equal(run.code, 1);
  const error = JSON.parse(run.stderr) as { error: { code: string } };
  assert.equal(error.error.code, "usage");
});

test("reports its own version", async () => {
  const run = await runCli(["--version"]);
  assert.equal(run.code, 0, run.stderr);
  assert.match(run.stdout.trim(), /^\d+\.\d+\.\d+$/);
});
