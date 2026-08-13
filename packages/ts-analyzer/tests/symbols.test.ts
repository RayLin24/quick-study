import test from "node:test";
import assert from "node:assert/strict";

import { analyzeProject } from "../src/index.ts";
import type { AnalysisResult, SymbolRecord } from "../src/index.ts";
import { fixturePath } from "./helpers.ts";

const result: AnalysisResult = await analyzeProject({ root: fixturePath("sample-project") });

function symbol(id: string): SymbolRecord {
  const found = result.symbols.find((entry) => entry.id === id);
  assert.ok(found, `expected symbol ${id}, got:\n${result.symbols.map((s) => s.id).join("\n")}`);
  return found;
}

test("reports every analyzed source file with content hash and language", () => {
  const paths = result.files.map((file) => file.path).sort();
  assert.deepEqual(paths, [
    "src/dynamic.ts",
    "src/helpers.js",
    "src/legacy.js",
    "src/registry.cjs",
    "src/service.ts",
    "src/util.ts",
  ]);

  const util = result.files.find((file) => file.path === "src/util.ts");
  assert.ok(util);
  assert.equal(util.language, "ts");
  assert.match(util.sha256, /^[0-9a-f]{64}$/);
  assert.ok(util.bytes > 0);
  assert.deepEqual(util.syntaxErrors, []);
});

test("extracts exported and internal function symbols", () => {
  const slugify = symbol("src/util.ts#slugify");
  assert.equal(slugify.kind, "function");
  assert.equal(slugify.exported, true);
  assert.equal(slugify.exportKind, "named");
  assert.equal(slugify.exportName, "slugify");
  assert.equal(slugify.range.startLine, 2);
  assert.equal(slugify.docSummary, "Normalizes a raw label into a comparable slug.");

  const internal = symbol("src/util.ts#internalOnly");
  assert.equal(internal.exported, false);
  assert.equal(internal.exportKind, null);
});

test("classifies type-level and value-level declaration kinds", () => {
  assert.equal(symbol("src/util.ts#Formatter").kind, "interface");
  assert.equal(symbol("src/util.ts#Locale").kind, "type-alias");
  assert.equal(symbol("src/util.ts#Level").kind, "enum");
  assert.equal(symbol("src/util.ts#DEFAULT_LOCALE").kind, "variable");
  assert.equal(symbol("src/util.ts#useInternal").kind, "function");
});

test("extracts class members with parent links and modifiers", () => {
  const repository = symbol("src/service.ts#Repository");
  assert.equal(repository.kind, "class");
  assert.equal(repository.exported, true);

  const load = symbol("src/service.ts#Repository.load");
  assert.equal(load.kind, "method");
  assert.equal(load.isAsync, true);
  assert.equal(load.parentId, repository.id);
  assert.equal(load.exported, false);

  assert.equal(symbol("src/service.ts#Repository.constructor").kind, "constructor");
  assert.equal(symbol("src/service.ts#Repository.label").kind, "getter");

  const staticKind = symbol("src/service.ts#Repository.kind");
  assert.equal(staticKind.kind, "property");
  assert.equal(staticKind.isStatic, true);
});

test("records default exports and plain JavaScript symbols", () => {
  const legacy = symbol("src/legacy.js#legacy");
  assert.equal(legacy.exportKind, "default");
  assert.equal(legacy.exported, true);

  assert.equal(symbol("src/helpers.js#describeLocale").kind, "function");
  assert.equal(symbol("src/registry.cjs#register").kind, "function");
});

test("orders symbols deterministically by file then position", () => {
  const keys = result.symbols.map((entry) => `${entry.file}:${entry.range.startLine}`);
  const sorted = [...result.symbols]
    .sort(
      (a, b) =>
        a.file.localeCompare(b.file) ||
        a.range.startLine - b.range.startLine ||
        a.range.startColumn - b.range.startColumn,
    )
    .map((entry) => `${entry.file}:${entry.range.startLine}`);
  assert.deepEqual(keys, sorted);
});
