import test from "node:test";
import assert from "node:assert/strict";

import { analyzeProject, DEFAULT_LIMITS } from "../src/index.ts";
import { makeTempTree } from "./helpers.ts";

test("only collects JavaScript and TypeScript sources", async () => {
  const root = makeTempTree({
    "src/a.ts": "export const a = 1;\n",
    "src/b.tsx": "export const b = () => null;\n",
    "src/c.mjs": "export const c = 1;\n",
    "src/d.cts": "export const d = 1;\n",
    "docs/readme.md": "# not source\n",
    "data/config.json": "{}\n",
    "assets/logo.png": "not really a png",
  });

  const result = await analyzeProject({ root });
  assert.deepEqual(
    result.files.map((file) => file.path).sort(),
    ["src/a.ts", "src/b.tsx", "src/c.mjs", "src/d.cts"],
  );
});

test("skips dependency, build and version-control directories", async () => {
  const root = makeTempTree({
    "src/app.ts": "export const app = 1;\n",
    "node_modules/dep/index.js": "module.exports = 1;\n",
    "dist/app.js": "export const app = 1;\n",
    "build/app.js": "export const app = 1;\n",
    "coverage/app.js": "export const app = 1;\n",
    ".git/hooks/pre-commit.js": "console.log(1);\n",
    "vendor/lib.js": "export const lib = 1;\n",
  });

  const result = await analyzeProject({ root });
  assert.deepEqual(result.files.map((file) => file.path), ["src/app.ts"]);
});

test("accepts explicit file entries instead of a whole directory", async () => {
  const root = makeTempTree({
    "src/a.ts": "export const a = 1;\n",
    "src/b.ts": "export const b = 2;\n",
  });

  const result = await analyzeProject({ root, entries: ["src/b.ts"] });
  assert.deepEqual(result.files.map((file) => file.path), ["src/b.ts"]);
});

test("skips individual files above the per-file byte limit", async () => {
  const root = makeTempTree({
    "src/small.ts": "export const small = 1;\n",
    "src/huge.ts": `export const huge = "${"x".repeat(5000)}";\n`,
  });

  const result = await analyzeProject({ root, limits: { maxFileBytes: 1024 } });
  assert.deepEqual(result.files.map((file) => file.path), ["src/small.ts"]);

  const diagnostic = result.diagnostics.find((entry) => entry.code === "limit.file-too-large");
  assert.ok(diagnostic, "expected a skip diagnostic for the oversized file");
  assert.equal(diagnostic.path, "src/huge.ts");
  assert.equal(result.limits.truncated, true);
});

test("stops collecting once the file count limit is reached", async () => {
  const root = makeTempTree({
    "src/a.ts": "export const a = 1;\n",
    "src/b.ts": "export const b = 1;\n",
    "src/c.ts": "export const c = 1;\n",
  });

  const result = await analyzeProject({ root, limits: { maxFiles: 2 } });
  assert.equal(result.files.length, 2);
  assert.equal(result.limits.truncated, true);
  assert.ok(result.limits.truncationReasons.includes("limit.max-files"));
  assert.ok(result.diagnostics.some((entry) => entry.code === "limit.max-files"));
});

test("stops collecting once the total byte budget is exhausted", async () => {
  const body = `export const value = "${"y".repeat(400)}";\n`;
  const root = makeTempTree({ "src/a.ts": body, "src/b.ts": body, "src/c.ts": body });

  const result = await analyzeProject({ root, limits: { maxTotalBytes: 700 } });
  assert.equal(result.files.length, 1);
  assert.ok(result.limits.truncationReasons.includes("limit.total-bytes"));
});

test("abandons analysis when the time budget is already spent", async () => {
  const root = makeTempTree({ "src/a.ts": "export const a = 1;\n" });

  const result = await analyzeProject({ root, limits: { timeBudgetMs: 0 } });
  assert.equal(result.limits.truncated, true);
  assert.ok(result.limits.truncationReasons.includes("limit.time-budget"));
});

test("refuses entries that escape the analysis root", async () => {
  const root = makeTempTree({ "src/a.ts": "export const a = 1;\n" });

  await assert.rejects(
    () => analyzeProject({ root, entries: ["../outside"] }),
    /outside the analysis root/i,
  );
});

test("rejects a limit override that exceeds the hard ceiling", async () => {
  const root = makeTempTree({ "src/a.ts": "export const a = 1;\n" });

  await assert.rejects(
    () => analyzeProject({ root, limits: { maxFiles: DEFAULT_LIMITS.maxFiles * 100 } }),
    /maxFiles/,
  );
});

test("reports the limits that were actually applied", async () => {
  const root = makeTempTree({ "src/a.ts": "export const a = 1;\n" });

  const result = await analyzeProject({ root, limits: { maxFiles: 10 } });
  assert.equal(result.limits.applied.maxFiles, 10);
  assert.equal(result.limits.applied.maxFileBytes, DEFAULT_LIMITS.maxFileBytes);
  assert.equal(result.limits.truncated, false);
  assert.deepEqual(result.limits.truncationReasons, []);
});
