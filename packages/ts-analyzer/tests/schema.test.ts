import test from "node:test";
import assert from "node:assert/strict";

import { analyzeProject, getOutputSchema, SCHEMA_VERSION } from "../src/index.ts";
import { fixturePath } from "./helpers.ts";

const result = await analyzeProject({ root: fixturePath("sample-project") });
const schema = getOutputSchema();

test("publishes a versioned schema identifier", () => {
  assert.match(SCHEMA_VERSION, /^\d+\.\d+\.\d+$/);
  assert.equal(result.schemaVersion, SCHEMA_VERSION);
  assert.match(String(schema["$id"]), new RegExp(SCHEMA_VERSION.replace(/\./g, "\\.")));
});

test("the schema describes exactly the top-level keys the analyzer emits", () => {
  const properties = schema["properties"] as Record<string, unknown>;
  assert.deepEqual(Object.keys(properties).sort(), Object.keys(result).sort());
  assert.deepEqual((schema["required"] as string[]).sort(), Object.keys(result).sort());
});

test("the schema pins the confidence and resolution vocabularies", () => {
  const definitions = schema["$defs"] as Record<string, { enum?: string[] }>;
  assert.deepEqual(definitions["confidence"]?.enum, ["high", "medium", "low"]);
  assert.deepEqual(definitions["callResolution"]?.enum, [
    "resolved",
    "external",
    "ambiguous",
    "unresolved",
  ]);
  assert.deepEqual(definitions["importResolution"]?.enum, ["internal", "external", "unresolved"]);
});

test("reports the analyzer and TypeScript versions used for the run", () => {
  assert.equal(result.tool.name, "@quick-study/ts-analyzer");
  assert.match(result.tool.version, /^\d+\.\d+\.\d+$/);
  assert.match(result.tool.typescript, /^\d+\.\d+\.\d+$/);
});

test("produces identical output for identical input apart from timing", async () => {
  const again = await analyzeProject({ root: fixturePath("sample-project") });
  const strip = (value: typeof result): string => {
    const { timing: _timing, ...rest } = value;
    return JSON.stringify(rest);
  };
  assert.equal(strip(result), strip(again));
});
