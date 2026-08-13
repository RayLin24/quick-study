import test from "node:test";
import assert from "node:assert/strict";

import { DEFAULT_LIMITS, inspectSource, resolveLimits } from "../src/index.ts";

const VALID = "flowchart LR\n  A[Start] --> B[End]\n";

test("accepts a plain diagram", () => {
  assert.equal(inspectSource(VALID, DEFAULT_LIMITS), null);
});

test("rejects empty input", () => {
  const error = inspectSource("   \n\t\n", DEFAULT_LIMITS);
  assert.ok(error);
  assert.equal(error.stage, "input");
  assert.equal(error.code, "input.empty");
});

test("rejects input above the byte limit", () => {
  const error = inspectSource(`${VALID}%% ${"x".repeat(200)}\n`, resolveLimits({ maxInputBytes: 64 }));
  assert.ok(error);
  assert.equal(error.code, "input.too-large");
});

test("rejects input above the line limit", () => {
  const source = `flowchart LR\n${"  A --> B\n".repeat(50)}`;
  const error = inspectSource(source, resolveLimits({ maxInputLines: 10 }));
  assert.ok(error);
  assert.equal(error.code, "input.too-many-lines");
});

test("rejects click interaction statements and points at the line", () => {
  const withUrl = inspectSource(
    'flowchart LR\n  A[Node] --> B[Other]\n  click A "https://evil.example/" _blank\n',
    DEFAULT_LIMITS,
  );
  assert.ok(withUrl);
  assert.equal(withUrl.code, "input.interaction-directive");
  assert.equal(withUrl.line, 3);

  const withCallback = inspectSource(
    'flowchart LR\n  A[Node]\n  click A callback "tooltip"\n',
    DEFAULT_LIMITS,
  );
  assert.ok(withCallback);
  assert.equal(withCallback.code, "input.interaction-directive");
});

test("rejects dangerous raw HTML tags in labels", () => {
  for (const label of [
    '<script>alert(1)</script>',
    '<img src=x onerror=alert(1)>',
    '<iframe src="https://evil.example"></iframe>',
    '<style>body{display:none}</style>',
  ]) {
    const error = inspectSource(`flowchart LR\n  A["${label}"] --> B[ok]\n`, DEFAULT_LIMITS);
    assert.ok(error, `expected ${label} to be rejected`);
    assert.equal(error.code, "input.raw-html");
  }
});

test("allows inert markup that Mermaid escapes by itself", () => {
  assert.equal(inspectSource('flowchart LR\n  A["<b>bold</b><br/>next"] --> B\n', DEFAULT_LIMITS), null);
});

test("rejects javascript: URLs anywhere in the source", () => {
  const error = inspectSource('flowchart LR\n  A["javascript:alert(1)"] --> B\n', DEFAULT_LIMITS);
  assert.ok(error);
  assert.equal(error.code, "input.unsafe-url");
});

test("rejects init directives that try to relax security", () => {
  for (const directive of [
    '%%{init: {"securityLevel": "loose"}}%%',
    '%%{init: {"flowchart": {"htmlLabels": true}}}%%',
    '%%{init: {"themeCSS": "@import url(https://evil.example/x.css);"}}%%',
    '%%{init: {"dompurifyConfig": {"ADD_TAGS": ["script"]}}}%%',
    '%%{init: {"secure": []}}%%',
  ]) {
    const error = inspectSource(`${directive}\n${VALID}`, DEFAULT_LIMITS);
    assert.ok(error, `expected ${directive} to be rejected`);
    assert.equal(error.code, "input.unsafe-init-directive");
  }
});

test("allows harmless init directives", () => {
  assert.equal(inspectSource(`%%{init: {"theme": "neutral"}}%%\n${VALID}`, DEFAULT_LIMITS), null);
});

test("clamps limit overrides to the hard ceilings", () => {
  assert.throws(
    () => resolveLimits({ maxInputBytes: DEFAULT_LIMITS.maxInputBytes * 1000 }),
    /maxInputBytes/,
  );
  assert.throws(() => resolveLimits({ renderTimeoutMs: -1 }), /renderTimeoutMs/);
});
