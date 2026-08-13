import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { renderDiagram, validateDiagram } from "../src/index.ts";
import { computeBoundingBox, parseTransformList, transformBox, type Box } from "../src/geometry.ts";

/** Bounding box of `element` expressed in the coordinate space of the `ancestor` group. */
function boxWithin(ancestor: Element, element: Element): Box | null {
  let box = computeBoundingBox(element);
  let current: Element | null = element;
  while (box && current && current !== ancestor) {
    box = transformBox(parseTransformList(current.getAttribute("transform")), box);
    current = current.parentElement;
  }
  return box;
}

const FLOWCHART =
  "flowchart LR\n  A[Ingestion Service] --> B{Needs Browser?}\n  B -->|yes| C[Playwright Sandbox]\n  B -->|no| D[Static Extractor]\n";
const SEQUENCE =
  "sequenceDiagram\n  participant Web as Next.js Web\n  participant API as FastAPI Control Plane\n  Web->>API: POST /projects\n  API-->>Web: 201 Created\n";
const CLASS_DIAGRAM = "classDiagram\n  class Repository {\n    +load() string\n  }\n  Repository --> Locale\n";

test("validates a diagram without rendering it", async () => {
  const result = await validateDiagram(FLOWCHART);
  assert.equal(result.ok, true);
  assert.equal(result.error, null);
  assert.equal(result.diagramType, "flowchart-v2");
});

test("reports Mermaid syntax errors with a diagnosable location", async () => {
  const result = await validateDiagram("flowchart LR\n  A --> ((((\n");
  assert.equal(result.ok, false);
  assert.ok(result.error);
  assert.equal(result.error.stage, "parse");
  assert.equal(result.error.code, "parse.syntax-error");
  assert.ok(result.error.message.length > 0);
  assert.equal(result.error.line, 2);
});

test("reports an unknown diagram type separately from a syntax error", async () => {
  const result = await validateDiagram("notADiagramType\n  A --> B\n");
  assert.equal(result.ok, false);
  assert.ok(result.error);
  assert.equal(result.error.code, "parse.unknown-diagram-type");
});

test("renders the three supported diagram families", async () => {
  for (const [name, source, expected] of [
    ["flowchart", FLOWCHART, "flowchart-v2"],
    ["sequence", SEQUENCE, "sequence"],
    ["class", CLASS_DIAGRAM, "class"],
  ] as const) {
    const result = await renderDiagram(source, { id: name });
    assert.equal(result.ok, true, `${name}: ${JSON.stringify(result.error)}`);
    assert.equal(result.diagramType, expected);
    assert.ok(result.svg);
    assert.match(result.svg, /^<svg /);
    assert.match(result.svg, /viewBox="/);
  }
});

test("the rendered SVG carries no script, handler or external reference", async () => {
  const result = await renderDiagram(FLOWCHART, { id: "safety" });
  assert.ok(result.svg);
  assert.ok(!/<script/i.test(result.svg));
  assert.ok(!/\son[a-z]+\s*=/i.test(result.svg));
  assert.ok(!/javascript:/i.test(result.svg));
  assert.ok(!/<foreignObject/i.test(result.svg));
  assert.ok(!/https?:\/\//i.test(result.svg.replace(/xmlns[^=]*="[^"]*"/g, "")));
});

test("the viewBox actually contains the laid out nodes", async () => {
  const result = await renderDiagram(FLOWCHART, { id: "layout" });
  assert.ok(result.svg);

  const viewBox = result.svg.match(/viewBox="([^"]+)"/);
  assert.ok(viewBox?.[1]);
  const [minX, minY, width, height] = viewBox[1].split(/[\s,]+/).map(Number) as [
    number,
    number,
    number,
    number,
  ];

  const translations = [
    ...result.svg.matchAll(/class="node[^"]*"[^>]*transform="translate\(([-\d.]+),\s*([-\d.]+)\)"/g),
  ].map((match) => ({ x: Number(match[1]), y: Number(match[2]) }));
  assert.ok(translations.length >= 4, "expected every flowchart node to be positioned");

  for (const point of translations) {
    assert.ok(
      point.x >= minX && point.x <= minX + width,
      `node at x=${point.x} falls outside viewBox ${viewBox[1]}`,
    );
    assert.ok(
      point.y >= minY && point.y <= minY + height,
      `node at y=${point.y} falls outside viewBox ${viewBox[1]}`,
    );
  }
  assert.ok(width > 0 && height > 0);
});

// Mermaid positions several shapes from the label's bounding box origin, so a bounding box that
// reports the wrong x pushes the label out of its shape even though the graph layout is right.
test("centres every node label inside its own shape", async () => {
  const source =
    "flowchart LR\n  A[Ingestion Service] --> B[(MySQL State)]\n  B --> C{Needs Browser?}\n  C --> D((Done))\n";
  const result = await renderDiagram(source, { id: "labels" });
  assert.ok(result.svg, JSON.stringify(result.error));

  const document = new JSDOM(`<!DOCTYPE html><body>${result.svg}</body>`).window.document;
  const nodes = [...document.querySelectorAll("g.node")];
  assert.ok(nodes.length >= 4, "expected every node to be laid out");

  for (const node of nodes) {
    const shape = node.querySelector("path.label-container, rect.label-container, polygon, circle");
    const text = node.querySelector("text");
    assert.ok(shape, "node is missing a shape");
    assert.ok(text, "node is missing a label");

    const shapeBox = boxWithin(node, shape);
    const textBox = boxWithin(node, text);
    assert.ok(shapeBox && textBox, "expected measurable geometry");

    const shapeCentre = shapeBox.x + shapeBox.width / 2;
    const textCentre = textBox.x + textBox.width / 2;
    assert.ok(
      Math.abs(shapeCentre - textCentre) <= Math.max(2, shapeBox.width * 0.15),
      `label "${text.textContent}" is centred on ${textCentre.toFixed(1)} but its shape on ${shapeCentre.toFixed(1)}`,
    );
  }
});

test("produces identical SVG for identical input", async () => {
  const first = await renderDiagram(FLOWCHART, { id: "stable" });
  const second = await renderDiagram(FLOWCHART, { id: "stable" });
  assert.equal(first.svg, second.svg);
});

test("returns no SVG at all when validation fails", async () => {
  const result = await renderDiagram("flowchart LR\n  A --> ((((\n", { id: "broken" });
  assert.equal(result.ok, false);
  assert.equal(result.svg, null);
  assert.ok(result.error);
  assert.equal(result.error.stage, "parse");
});

test("rejects interaction directives before Mermaid ever sees them", async () => {
  const result = await renderDiagram(
    'flowchart LR\n  A[Node] --> B[Other]\n  click A "https://evil.example/" _blank\n',
    { id: "click" },
  );
  assert.equal(result.ok, false);
  assert.equal(result.svg, null);
  assert.ok(result.error);
  assert.equal(result.error.stage, "input");
  assert.equal(result.error.code, "input.interaction-directive");
});

test("refuses to emit output above the size limit", async () => {
  const result = await renderDiagram(FLOWCHART, { id: "big", limits: { maxOutputBytes: 512 } });
  assert.equal(result.ok, false);
  assert.equal(result.svg, null);
  assert.ok(result.error);
  assert.equal(result.error.code, "render.output-too-large");
});

test("gives up when the render budget is exhausted", async () => {
  const result = await renderDiagram(FLOWCHART, { id: "slow", limits: { renderTimeoutMs: 0 } });
  assert.equal(result.ok, false);
  assert.equal(result.svg, null);
  assert.ok(result.error);
  assert.equal(result.error.code, "render.timeout");
});

test("reports sanitization and size statistics", async () => {
  const result = await renderDiagram(FLOWCHART, { id: "stats" });
  assert.ok(result.svg);
  assert.equal(result.stats.inputBytes, Buffer.byteLength(FLOWCHART, "utf8"));
  assert.equal(result.stats.outputBytes, Buffer.byteLength(result.svg, "utf8"));
  assert.ok(result.stats.durationMs >= 0);
  assert.ok(Array.isArray(result.sanitization.removedElements));
});

test("one malformed diagram does not poison the next render", async () => {
  await renderDiagram("flowchart LR\n  A --> ((((\n", { id: "poison" });
  const result = await renderDiagram(FLOWCHART, { id: "after-poison" });
  assert.equal(result.ok, true, JSON.stringify(result.error));
});
