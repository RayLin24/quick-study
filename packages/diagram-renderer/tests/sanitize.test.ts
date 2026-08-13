import test from "node:test";
import assert from "node:assert/strict";

import { sanitizeSvg } from "../src/index.ts";

const SVG_OPEN =
  '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 100 100">';

function sanitize(body: string): { svg: string; removedElements: string[]; removedAttributes: string[] } {
  const outcome = sanitizeSvg(`${SVG_OPEN}${body}</svg>`);
  assert.ok(outcome.ok, `sanitization failed: ${JSON.stringify(outcome)}`);
  return {
    svg: outcome.svg,
    removedElements: outcome.report.removedElements.map((entry) => entry.name),
    removedAttributes: outcome.report.removedAttributes.map((entry) => entry.name),
  };
}

test("removes script elements and reports them", () => {
  const result = sanitize('<script>alert(1)</script><rect x="1" y="2" width="3" height="4"/>');
  assert.ok(!/<script/i.test(result.svg));
  assert.ok(!/alert\(1\)/.test(result.svg));
  assert.ok(result.removedElements.includes("script"));
  assert.match(result.svg, /<rect/);
});

test("removes event handler attributes while keeping the element", () => {
  const result = sanitize('<rect onclick="alert(1)" onload="steal()" x="1" width="3" height="4"/>');
  assert.ok(!/onclick/i.test(result.svg));
  assert.ok(!/onload/i.test(result.svg));
  assert.match(result.svg, /<rect/);
  assert.match(result.svg, /width="3"/);
  assert.ok(result.removedAttributes.includes("onclick"));
});

test("removes external and scripted references but keeps fragment links", () => {
  const external = sanitize('<a xlink:href="https://evil.example/x"><rect width="1" height="1"/></a>');
  assert.ok(!/evil\.example/.test(external.svg));
  assert.match(external.svg, /<rect/);

  const scripted = sanitize('<a href="javascript:alert(1)"><rect width="1" height="1"/></a>');
  assert.ok(!/javascript:/i.test(scripted.svg));

  const fragment = sanitize('<use href="#marker"/>');
  assert.match(fragment.svg, /href="#marker"/);

  const remote = sanitize('<use href="https://evil.example/sprite.svg#icon"/>');
  assert.ok(!/evil\.example/.test(remote.svg));

  const dataUri = sanitize('<image href="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4="/>');
  assert.ok(!/data:/.test(dataUri.svg));
});

test("removes foreignObject and any HTML smuggled through it", () => {
  const result = sanitize('<foreignObject><div onclick="alert(1)">hi</div></foreignObject><rect width="1" height="1"/>');
  assert.ok(!/foreignObject/i.test(result.svg));
  assert.ok(!/<div/i.test(result.svg));
  assert.ok(result.removedElements.includes("foreignobject"));
});

test("strips external imports and remote urls from style blocks", () => {
  const result = sanitize(
    '<style>@import url(https://evil.example/x.css); .node{fill:red;background:url(https://evil.example/y.png)}</style><rect width="1" height="1"/>',
  );
  assert.ok(!/@import/i.test(result.svg));
  assert.ok(!/evil\.example/.test(result.svg));
  assert.match(result.svg, /fill:red/);
});

test("strips scripted style attributes", () => {
  const result = sanitize('<rect style="fill:url(javascript:alert(1))" width="1" height="1"/>');
  assert.ok(!/javascript:/i.test(result.svg));
  assert.match(result.svg, /<rect/);
});

test("removes comments and processing instructions", () => {
  const result = sanitize('<!-- smuggled --><rect width="1" height="1"/>');
  assert.ok(!/smuggled/.test(result.svg));
});

test("keeps the svg root and its namespace", () => {
  const result = sanitize('<rect width="1" height="1"/>');
  assert.match(result.svg, /^<svg /);
  assert.match(result.svg, /xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.match(result.svg, /viewBox="0 0 100 100"/);
});

test("rejects input whose root element is not an svg", () => {
  const outcome = sanitizeSvg('<div><script>alert(1)</script></div>');
  assert.equal(outcome.ok, false);
  assert.ok(!outcome.ok);
  assert.equal(outcome.error.stage, "sanitize");
  assert.equal(outcome.error.code, "sanitize.no-svg-root");
});

test("rejects an empty document", () => {
  const outcome = sanitizeSvg("");
  assert.equal(outcome.ok, false);
  assert.ok(!outcome.ok);
  assert.equal(outcome.error.code, "sanitize.no-svg-root");
});
