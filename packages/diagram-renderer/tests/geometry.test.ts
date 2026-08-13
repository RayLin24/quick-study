import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";

import { computeBoundingBox, parseTransformList } from "../src/geometry.ts";

const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>");

function svgDocument(markup: string): Document {
  return new dom.window.DOMParser().parseFromString(
    `<svg xmlns="http://www.w3.org/2000/svg">${markup}</svg>`,
    "image/svg+xml",
  ) as unknown as Document;
}

function svgElement(markup: string): SVGElement {
  const child = svgDocument(markup).documentElement.firstElementChild;
  assert.ok(child);
  return child as unknown as SVGElement;
}

test("parses translate, scale and their composition", () => {
  assert.deepEqual(parseTransformList("translate(10, 20)"), { a: 1, b: 0, c: 0, d: 1, e: 10, f: 20 });
  assert.deepEqual(parseTransformList("scale(2)"), { a: 2, b: 0, c: 0, d: 2, e: 0, f: 0 });
  assert.deepEqual(parseTransformList("translate(10,20) scale(2)"), {
    a: 2,
    b: 0,
    c: 0,
    d: 2,
    e: 10,
    f: 20,
  });
  assert.deepEqual(parseTransformList(""), { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
});

test("measures basic shapes", () => {
  assert.deepEqual(computeBoundingBox(svgElement('<rect x="10" y="20" width="30" height="40"/>')), {
    x: 10,
    y: 20,
    width: 30,
    height: 40,
  });
  assert.deepEqual(computeBoundingBox(svgElement('<circle cx="50" cy="60" r="10"/>')), {
    x: 40,
    y: 50,
    width: 20,
    height: 20,
  });
  assert.deepEqual(computeBoundingBox(svgElement('<line x1="5" y1="5" x2="15" y2="25"/>')), {
    x: 5,
    y: 5,
    width: 10,
    height: 20,
  });
});

function assertBoxNear(actual: ReturnType<typeof computeBoundingBox>, expected: {
  x: number;
  y: number;
  width: number;
  height: number;
}): void {
  assert.ok(actual);
  for (const key of ["x", "y", "width", "height"] as const) {
    assert.ok(
      Math.abs(actual[key] - expected[key]) < 0.01,
      `${key}: expected ${expected[key]}, got ${actual[key]}`,
    );
  }
}

test("measures paths from their commands, not from every number in the data", () => {
  assertBoxNear(computeBoundingBox(svgElement('<path d="M0,0 L10,20"/>')), {
    x: 0,
    y: 0,
    width: 10,
    height: 20,
  });
  assertBoxNear(computeBoundingBox(svgElement('<path d="M0,0 h10 v20 Z"/>')), {
    x: 0,
    y: 0,
    width: 10,
    height: 20,
  });
  assertBoxNear(computeBoundingBox(svgElement('<path d="M5,5 c5,0 10,5 10,10"/>')), {
    x: 5,
    y: 5,
    width: 10,
    height: 10,
  });
});

// Mermaid draws cylinders as a horizontal chord bulging by ry. Treating the arc's radii and
// flags as coordinates produces a wildly wrong box and distorts the whole node.
test("bounds an elliptical arc by its chord plus the perpendicular radius", () => {
  assertBoxNear(computeBoundingBox(svgElement('<path d="M0,11.8 a55.9,11.8 0 0 0 111.8,0"/>')), {
    x: 0,
    y: 0,
    width: 111.8,
    height: 23.6,
  });
});

test("applies child transforms when unioning a group", () => {
  const box = computeBoundingBox(
    svgElement('<g><g transform="translate(100, 50)"><rect x="0" y="0" width="10" height="10"/></g></g>'),
  );
  assert.deepEqual(box, { x: 100, y: 50, width: 10, height: 10 });
});

test("unions sibling children", () => {
  const box = computeBoundingBox(
    svgElement(
      '<g><rect x="0" y="0" width="10" height="10"/><rect x="90" y="40" width="10" height="10"/></g>',
    ),
  );
  assert.deepEqual(box, { x: 0, y: 0, width: 100, height: 50 });
});

test("estimates text extents from the content, honouring text-anchor", () => {
  const start = computeBoundingBox(svgElement('<text x="0" y="0" font-size="10">abcd</text>'));
  assert.ok(start);
  assert.ok(start.width > 0, "text must have a measurable width");
  assert.equal(start.x, 0);

  const middle = computeBoundingBox(
    svgElement('<text x="0" y="0" font-size="10" text-anchor="middle">abcd</text>'),
  );
  assert.ok(middle);
  assert.equal(middle.x, -middle.width / 2);
});

// Mermaid centres node labels with a stylesheet rule rather than an attribute, and it measures
// labels before that stylesheet is attached. Measuring such text as start-anchored makes Mermaid
// shift the label by half its width, which pushes labels out of shapes that position their label
// from the bounding box origin, so the caller can declare the default that will apply.
test("lets the caller declare the default text-anchor", () => {
  const centred = computeBoundingBox(svgElement('<text x="0" y="0" font-size="10">abcd</text>'), {
    defaultTextAnchor: "middle",
  });
  assert.ok(centred);
  assert.ok(centred.width > 0);
  assert.equal(centred.x, -centred.width / 2);

  const explicit = computeBoundingBox(
    svgElement('<text x="0" y="0" font-size="10" text-anchor="start">abcd</text>'),
    { defaultTextAnchor: "middle" },
  );
  assert.ok(explicit);
  assert.equal(explicit.x, 0);
});

test("honours text-anchor inherited from a stylesheet rule", () => {
  const document = svgDocument(
    '<style>#d .node .label text{text-anchor:middle;}</style>' +
      '<g id="d"><g class="node"><g class="label"><text x="0" y="0" font-size="10">abcd</text></g></g></g>',
  );
  const text = document.querySelector("text");
  assert.ok(text);

  const box = computeBoundingBox(text);
  assert.ok(box);
  assert.ok(box.width > 0);
  assert.equal(box.x, -box.width / 2);
});

test("an explicit attribute still wins over an unrelated stylesheet rule", () => {
  const document = svgDocument(
    '<style>.other text{text-anchor:middle;}</style>' +
      '<g class="node"><text x="0" y="0" font-size="10" text-anchor="start">abcd</text></g>',
  );
  const text = document.querySelector("text");
  assert.ok(text);

  const box = computeBoundingBox(text);
  assert.ok(box);
  assert.equal(box.x, 0);
});

// Mermaid puts the baseline on a row <tspan> with em-based y/dy rather than on the <text>, and
// several shapes place their label from bbox.y, so ignoring those offsets shifts labels
// vertically out of their shape.
test("resolves tspan y and dy to locate the baseline", () => {
  const single = computeBoundingBox(
    svgElement('<text y="-10.1" font-size="16"><tspan x="0" y="-0.1em" dy="1.1em">abcd</tspan></text>'),
  );
  assert.ok(single);
  // Baseline: -0.1em + 1.1em = 16. Top sits one ascender above it.
  assert.ok(Math.abs(single.y - (16 - 16 * 0.8)) < 0.01, `got y=${single.y}`);

  const stacked = computeBoundingBox(
    svgElement('<text y="0" font-size="10"><tspan x="0" dy="1em">a</tspan><tspan x="0" dy="1em">bb</tspan></text>'),
  );
  assert.ok(stacked);
  // Baselines at 10 and 20, so the box spans one ascender above the first to one descender
  // below the last: ascender 0.8em, descender 0.45em at a 1.25em line height.
  const ascender = 10 * 0.8;
  const descender = 10 * (1.25 - 0.8);
  assert.ok(Math.abs(stacked.y - (10 - ascender)) < 0.01, `got y=${stacked.y}`);
  assert.ok(
    Math.abs(stacked.height - (20 + descender - (10 - ascender))) < 0.01,
    `got height=${stacked.height}`,
  );
});

test("returns null for elements with no geometry", () => {
  assert.equal(computeBoundingBox(svgElement("<g></g>")), null);
});
