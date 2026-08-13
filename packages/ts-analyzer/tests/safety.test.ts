import test from "node:test";
import assert from "node:assert/strict";
import { existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { analyzeProject } from "../src/index.ts";
import { fixturePath } from "./helpers.ts";

test("never executes the analyzed repository", async () => {
  const marker = path.join(tmpdir(), `ts-analyzer-side-effect-${process.pid}.txt`);
  rmSync(marker, { force: true });
  process.env.TS_ANALYZER_SIDE_EFFECT_MARKER = marker;

  try {
    const result = await analyzeProject({ root: fixturePath("side-effect-project") });
    assert.equal(result.files.length, 1);
    assert.equal(
      existsSync(marker),
      false,
      "analysis must be purely static: the fixture's top-level code ran",
    );
  } finally {
    delete process.env.TS_ANALYZER_SIDE_EFFECT_MARKER;
    rmSync(marker, { force: true });
  }
});

test("reports syntax errors as data instead of throwing", async () => {
  const result = await analyzeProject({ root: fixturePath("broken-project") });

  const file = result.files.find((entry) => entry.path === "src/broken.ts");
  assert.ok(file);
  assert.ok(file.syntaxErrors.length > 0, "expected at least one syntax error");

  const first = file.syntaxErrors[0];
  assert.ok(first);
  assert.ok(first.message.length > 0);
  assert.ok(first.line >= 1);

  assert.ok(result.diagnostics.some((entry) => entry.code === "parse.syntax-error"));
});

test("does not read files outside the analysis root while resolving modules", async () => {
  const result = await analyzeProject({ root: fixturePath("sample-project") });

  for (const file of result.files) {
    assert.ok(!file.path.startsWith(".."), `escaped the root: ${file.path}`);
    assert.ok(!path.isAbsolute(file.path), `paths must be root-relative: ${file.path}`);
  }
  for (const record of result.imports) {
    if (record.resolvedFile !== null) {
      assert.ok(
        result.files.some((file) => file.path === record.resolvedFile),
        `resolved to a file outside the analysis set: ${record.resolvedFile}`,
      );
    }
  }
});
