import test from "node:test";
import assert from "node:assert/strict";

import { analyzeProject } from "../src/index.ts";
import type { AnalysisResult, ImportRecord } from "../src/index.ts";
import { fixturePath } from "./helpers.ts";

const result: AnalysisResult = await analyzeProject({ root: fixturePath("sample-project") });

function imports(file: string, specifier: string): ImportRecord[] {
  return result.imports.filter(
    (entry) => entry.file === file && entry.moduleSpecifier === specifier,
  );
}

function single(
  file: string,
  specifier: string,
  kind: ImportRecord["kind"],
  typeOnly = false,
): ImportRecord {
  const matches = imports(file, specifier).filter(
    (entry) => entry.kind === kind && entry.typeOnly === typeOnly,
  );
  assert.equal(matches.length, 1, `expected one ${kind} import of ${specifier} in ${file}`);
  return matches[0] as ImportRecord;
}

test("resolves relative imports to files inside the analysis set", () => {
  const record = single("src/service.ts", "./util", "static");
  assert.equal(record.resolution, "internal");
  assert.equal(record.resolvedFile, "src/util.ts");
  assert.equal(record.typeOnly, false);
  assert.equal(record.unresolvedReason, null);
  assert.deepEqual(record.bindings, [{ imported: "slugify", local: "slugify", kind: "named" }]);
});

test("marks type-only imports so they never imply a runtime dependency", () => {
  const typeImport = imports("src/service.ts", "./util").find((entry) => entry.typeOnly);
  assert.ok(typeImport);
  assert.equal(typeImport.resolution, "internal");
  assert.deepEqual(typeImport.bindings, [{ imported: "Locale", local: "Locale", kind: "named" }]);
});

test("captures namespace, default and re-export bindings", () => {
  const namespaceImport = single("src/service.ts", "./helpers", "static");
  assert.deepEqual(namespaceImport.bindings, [
    { imported: "*", local: "helpers", kind: "namespace" },
  ]);
  assert.equal(namespaceImport.resolvedFile, "src/helpers.js");

  const defaultImport = single("src/service.ts", "./legacy.js", "static");
  assert.deepEqual(defaultImport.bindings, [
    { imported: "default", local: "legacy", kind: "default" },
  ]);
  assert.equal(defaultImport.resolvedFile, "src/legacy.js");

  const reExport = single("src/service.ts", "./util", "re-export");
  assert.equal(reExport.resolution, "internal");
  assert.deepEqual(reExport.bindings, [
    { imported: "titleCase", local: "titleCase", kind: "named" },
  ]);
});

test("classifies bare specifiers as external without inventing a target file", () => {
  const builtin = single("src/service.ts", "node:fs/promises", "static");
  assert.equal(builtin.resolution, "external");
  assert.equal(builtin.resolvedFile, null);

  const thirdParty = single("src/service.ts", "chalk", "static");
  assert.equal(thirdParty.resolution, "external");
  assert.equal(thirdParty.resolvedFile, null);
});

test("captures CommonJS require calls", () => {
  const internalRequire = single("src/registry.cjs", "./util", "require");
  assert.equal(internalRequire.resolution, "internal");
  assert.equal(internalRequire.resolvedFile, "src/util.ts");

  const externalRequire = single("src/registry.cjs", "definitely-not-installed", "require");
  assert.equal(externalRequire.resolution, "external");
  assert.equal(externalRequire.resolvedFile, null);
});

test("flags dynamic import specifiers as unresolved instead of guessing", () => {
  const dynamic = result.imports.filter(
    (entry) => entry.file === "src/dynamic.ts" && entry.kind === "dynamic",
  );
  const nonLiteral = dynamic.find((entry) => entry.resolution === "unresolved");
  assert.ok(nonLiteral, "expected the template-literal import to be unresolved");
  assert.equal(nonLiteral.resolvedFile, null);
  assert.equal(nonLiteral.unresolvedReason, "dynamic-specifier");

  const literal = dynamic.find((entry) => entry.moduleSpecifier === "./util");
  assert.ok(literal);
  assert.equal(literal.resolution, "internal");
  assert.equal(literal.resolvedFile, "src/util.ts");
});

test("aggregates file-to-file and file-to-package dependency counts", () => {
  const internal = result.dependencies.find(
    (edge) => edge.scope === "internal" && edge.from === "src/service.ts" && edge.to === "src/util.ts",
  );
  assert.ok(internal);
  assert.equal(internal.count, 3);

  const external = result.dependencies.find(
    (edge) => edge.scope === "external" && edge.from === "src/service.ts" && edge.to === "chalk",
  );
  assert.ok(external);
  assert.equal(external.count, 1);

  const missingPackage = result.dependencies.find(
    (edge) => edge.scope === "external" && edge.to === "definitely-not-installed",
  );
  assert.ok(missingPackage);
});
