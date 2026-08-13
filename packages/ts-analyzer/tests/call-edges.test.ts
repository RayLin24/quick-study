import test from "node:test";
import assert from "node:assert/strict";

import { analyzeProject } from "../src/index.ts";
import type { AnalysisResult, CallEdge } from "../src/index.ts";
import { fixturePath } from "./helpers.ts";

const result: AnalysisResult = await analyzeProject({ root: fixturePath("sample-project") });

function edgesFrom(symbolId: string): CallEdge[] {
  return result.callEdges.filter((edge) => edge.from === symbolId);
}

function edge(symbolId: string, calleeText: string): CallEdge {
  const found = edgesFrom(symbolId).find((entry) => entry.calleeText === calleeText);
  assert.ok(
    found,
    `expected call to "${calleeText}" from ${symbolId}, got:\n${edgesFrom(symbolId)
      .map((entry) => `${entry.calleeText} [${entry.resolution}/${entry.reason}]`)
      .join("\n")}`,
  );
  return found;
}

test("resolves same-file function calls with high confidence", () => {
  const call = edge("src/util.ts#titleCase", "slugify");
  assert.equal(call.resolution, "resolved");
  assert.equal(call.to, "src/util.ts#slugify");
  assert.equal(call.confidence, "high");
  assert.equal(call.reason, "checker-unique-declaration");
  assert.equal(call.callKind, "function");
  assert.equal(call.fromFile, "src/util.ts");
});

test("resolves cross-file calls through named and namespace imports", () => {
  const viaImport = edge("src/helpers.js#describeLocale", "slugify");
  assert.equal(viaImport.to, "src/util.ts#slugify");
  assert.equal(viaImport.confidence, "high");

  const viaNamespace = edge("src/service.ts#Repository.describe", "helpers.describeLocale");
  assert.equal(viaNamespace.resolution, "resolved");
  assert.equal(viaNamespace.to, "src/helpers.js#describeLocale");
  assert.equal(viaNamespace.confidence, "high");
});

test("downgrades instance method calls because runtime dispatch may override them", () => {
  const call = edge("src/service.ts#bootstrap", "repo.load");
  assert.equal(call.resolution, "resolved");
  assert.equal(call.to, "src/service.ts#Repository.load");
  assert.equal(call.confidence, "medium");
  assert.equal(call.callKind, "method");
});

test("records constructor calls as edges to the class symbol", () => {
  const call = edge("src/service.ts#bootstrap", "Repository");
  assert.equal(call.callKind, "constructor");
  assert.equal(call.to, "src/service.ts#Repository");
  assert.equal(call.confidence, "high");
});

test("attributes calls into third-party and builtin modules without a local target", () => {
  const builtin = edge("src/service.ts#Repository.load", "readFile");
  assert.equal(builtin.resolution, "external");
  assert.equal(builtin.to, null);
  assert.equal(builtin.externalModule, "node:fs/promises");
  assert.equal(builtin.confidence, "medium");

  const thirdParty = edge("src/service.ts#Repository.label", "chalk.bold");
  assert.equal(thirdParty.resolution, "external");
  assert.equal(thirdParty.to, null);
  assert.equal(thirdParty.externalModule, "chalk");
});

test("marks dynamic dispatch as unresolved with a diagnosable reason", () => {
  const anyCall = edge("src/dynamic.ts#dispatchByName", "target.run");
  assert.equal(anyCall.resolution, "unresolved");
  assert.equal(anyCall.to, null);
  assert.equal(anyCall.confidence, "low");
  assert.equal(anyCall.reason, "callee-type-any");

  const computed = edge("src/dynamic.ts#dispatchByName", "registry[method]");
  assert.equal(computed.resolution, "unresolved");
  assert.equal(computed.to, null);
  assert.equal(computed.reason, "computed-member-access");
  assert.equal(computed.callKind, "computed");

  const indexSignature = edge("src/dynamic.ts#dispatchByName", "registry.first");
  assert.equal(indexSignature.resolution, "unresolved");
  assert.equal(indexSignature.to, null);
});

test("marks callback parameter invocation as unresolved", () => {
  const call = edge("src/dynamic.ts#invokeCallback", "callback");
  assert.equal(call.resolution, "unresolved");
  assert.equal(call.to, null);
  assert.equal(call.confidence, "low");
  assert.equal(call.reason, "parameter-invocation");
});

test("marks eval and Function construction as unresolved reflection", () => {
  const evalCall = edge("src/dynamic.ts#evaluate", "eval");
  assert.equal(evalCall.resolution, "unresolved");
  assert.equal(evalCall.reason, "dynamic-eval");

  const functionCall = edge("src/dynamic.ts#compile", "Function");
  assert.equal(functionCall.resolution, "unresolved");
  assert.equal(functionCall.reason, "dynamic-eval");
});

test("marks calls through local bindings as unresolved rather than tracked symbols", () => {
  const call = edge("src/dynamic.ts#useLocal", "local");
  assert.equal(call.resolution, "unresolved");
  assert.equal(call.to, null);
  assert.equal(call.reason, "local-binding");

  const nested = edge("src/dynamic.ts#useLocal", "slugify");
  assert.equal(nested.resolution, "resolved");
  assert.equal(nested.to, "src/util.ts#slugify");
});

test("treats non-literal dynamic import as an unresolved edge", () => {
  const call = edgesFrom("src/dynamic.ts#loadPlugin").find(
    (entry) => entry.callKind === "dynamic-import",
  );
  assert.ok(call);
  assert.equal(call.resolution, "unresolved");
  assert.equal(call.to, null);
  assert.equal(call.reason, "dynamic-import-non-literal");

  const known = edgesFrom("src/dynamic.ts#loadKnown").find(
    (entry) => entry.callKind === "dynamic-import",
  );
  assert.ok(known);
  assert.equal(known.resolution, "resolved");
  assert.equal(known.to, null);
  assert.equal(known.resolvedFile, "src/util.ts");
});

test("attributes top-level calls to the file rather than to a fabricated symbol", () => {
  const topLevel = result.callEdges.filter(
    (entry) => entry.fromFile === "src/registry.cjs" && entry.from === null,
  );
  assert.ok(topLevel.length >= 2, "expected top-level require() calls with a null owner");
});

test("never emits a resolved target unless the symbol exists in the output", () => {
  const ids = new Set(result.symbols.map((entry) => entry.id));
  for (const call of result.callEdges) {
    if (call.to !== null) {
      assert.ok(ids.has(call.to), `call edge points at unknown symbol ${call.to}`);
      assert.equal(call.resolution, "resolved");
    }
    if (call.resolution === "unresolved") {
      assert.equal(call.to, null);
      assert.equal(call.confidence, "low");
      assert.ok(call.reason.length > 0);
    }
  }
});

test("summarises resolved versus unresolved edges in stats", () => {
  assert.equal(
    result.stats.callEdgeCount,
    result.callEdges.length,
    "stats must match the emitted edges",
  );
  assert.equal(
    result.stats.unresolvedCallEdgeCount,
    result.callEdges.filter((entry) => entry.resolution === "unresolved").length,
  );
  assert.ok(result.stats.unresolvedCallEdgeCount > 0);
});
