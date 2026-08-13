import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { fixturePath, packageRoot } from "./helpers.ts";

const cliEntry = path.join(packageRoot(), "src", "cli.ts");

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
    if (stdin !== undefined) {
      child.stdin.end(stdin);
    } else {
      child.stdin.end();
    }
  });
}

test("prints the JSON schema for the analysis output", async () => {
  const run = await runCli(["--print-schema"]);
  assert.equal(run.code, 0, run.stderr);

  const schema = JSON.parse(run.stdout) as Record<string, unknown>;
  assert.equal(typeof schema["$id"], "string");
  assert.equal(schema["type"], "object");
  assert.ok(schema["properties"]);
});

test("writes a single JSON document to stdout", async () => {
  const run = await runCli(["--root", fixturePath("sample-project")]);
  assert.equal(run.code, 0, run.stderr);

  const result = JSON.parse(run.stdout) as { files: unknown[]; schemaVersion: string };
  assert.equal(result.files.length, 6);
  assert.equal(typeof result.schemaVersion, "string");
  assert.equal(run.stdout.trimStart()[0], "{", "stdout must contain nothing but the JSON document");
});

test("writes to a file and leaves stdout empty when --out is given", async () => {
  const outDir = mkdtempSync(path.join(tmpdir(), "ts-analyzer-cli-"));
  const outFile = path.join(outDir, "analysis.json");

  const run = await runCli(["--root", fixturePath("sample-project"), "--out", outFile]);
  assert.equal(run.code, 0, run.stderr);
  assert.equal(run.stdout.trim(), "");

  const parsed = JSON.parse(readFileSync(outFile, "utf8")) as { files: unknown[] };
  assert.equal(parsed.files.length, 6);
});

test("reads an explicit file list from stdin", async () => {
  const run = await runCli(
    ["--root", fixturePath("sample-project"), "--files-from", "-"],
    "src/util.ts\nsrc/helpers.js\n",
  );
  assert.equal(run.code, 0, run.stderr);

  const result = JSON.parse(run.stdout) as { files: { path: string }[] };
  assert.deepEqual(
    result.files.map((file) => file.path).sort(),
    ["src/helpers.js", "src/util.ts"],
  );
});

test("emits machine-readable usage errors on stderr with exit code 1", async () => {
  const run = await runCli(["--definitely-not-an-option"]);
  assert.equal(run.code, 1);
  assert.equal(run.stdout, "");

  const error = JSON.parse(run.stderr) as { error: { code: string; message: string } };
  assert.equal(error.error.code, "usage");
  assert.match(error.error.message, /--definitely-not-an-option/);
});

test("exits with code 2 when --strict-limits trips a limit", async () => {
  const run = await runCli([
    "--root",
    fixturePath("sample-project"),
    "--max-files",
    "1",
    "--strict-limits",
  ]);
  assert.equal(run.code, 2);
  assert.equal(run.stdout, "");

  const error = JSON.parse(run.stderr) as { error: { code: string; truncationReasons: string[] } };
  assert.equal(error.error.code, "limit-exceeded");
  assert.ok(error.error.truncationReasons.includes("limit.max-files"));
});

test("keeps stdout parseable when the sources contain syntax errors", async () => {
  const run = await runCli(["--root", fixturePath("broken-project")]);
  assert.equal(run.code, 0, run.stderr);

  const result = JSON.parse(run.stdout) as { diagnostics: { code: string }[] };
  assert.ok(result.diagnostics.some((entry) => entry.code === "parse.syntax-error"));
});

test("produces byte-identical output across runs apart from timing", async () => {
  const args = ["--root", fixturePath("sample-project")];
  const [first, second] = await Promise.all([runCli(args), runCli(args)]);
  assert.equal(first.code, 0, first.stderr);
  assert.equal(second.code, 0, second.stderr);

  const strip = (raw: string): string => {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    delete parsed["timing"];
    return JSON.stringify(parsed);
  };
  assert.equal(strip(first.stdout), strip(second.stdout));
});

test("reports its own version", async () => {
  const run = await runCli(["--version"]);
  assert.equal(run.code, 0, run.stderr);
  assert.match(run.stdout.trim(), /^\d+\.\d+\.\d+$/);
});
