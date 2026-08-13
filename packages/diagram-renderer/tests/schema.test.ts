import test from "node:test";
import assert from "node:assert/strict";

import { getResultSchema, MERMAID_VERSION, renderDiagram, SCHEMA_VERSION } from "../src/index.ts";

const schema = getResultSchema();

test("publishes a versioned schema identifier", () => {
  assert.match(SCHEMA_VERSION, /^\d+\.\d+\.\d+$/);
  assert.match(String(schema["$id"]), new RegExp(SCHEMA_VERSION.replace(/\./g, "\\.")));
});

test("the schema describes exactly the keys a render emits", async () => {
  const result = await renderDiagram("flowchart LR\n  A --> B\n", { id: "schema" });
  const properties = schema["properties"] as Record<string, unknown>;
  assert.deepEqual(Object.keys(properties).sort(), Object.keys(result).sort());
  assert.deepEqual((schema["required"] as string[]).sort(), Object.keys(result).sort());
});

test("the schema pins the failure stages and mermaid version", () => {
  const definitions = schema["$defs"] as Record<string, { enum?: string[] }>;
  assert.deepEqual(definitions["stage"]?.enum, ["input", "parse", "render", "sanitize"]);
  assert.match(MERMAID_VERSION, /^\d+\.\d+\.\d+$/);
});

test("reports the pinned Mermaid version in the result", async () => {
  const result = await renderDiagram("flowchart LR\n  A --> B\n", { id: "version" });
  assert.equal(result.tool.mermaid, MERMAID_VERSION);
  assert.equal(result.tool.name, "@quick-study/diagram-renderer");
});
