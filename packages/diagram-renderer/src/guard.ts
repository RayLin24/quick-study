import type { DiagramError, Limits } from "./types.ts";

/**
 * Mermaid interaction statements. `securityLevel: "strict"` disables the JavaScript callback
 * form but still emits `<a href>` for the URL form, so the source is rejected outright.
 */
const INTERACTION_STATEMENT = /^\s*(click|callback)\s+\S/i;

/** Tags that must never reach a label, even though Mermaid escapes most markup in strict mode. */
const DANGEROUS_TAG =
  /<\s*\/?\s*(script|iframe|object|embed|link|meta|style|img|svg|foreignObject|form|input|button|base|animate|set|handler)\b/i;

const UNSAFE_URL = /(javascript|vbscript|data)\s*:/i;

/** Init directive keys that would weaken the renderer's security posture. */
const FORBIDDEN_DIRECTIVE_KEYS = [
  "securityLevel",
  "secure",
  "startOnLoad",
  "htmlLabels",
  "dompurifyConfig",
  "themeCSS",
  "altFontFamily",
];

const INIT_DIRECTIVE = /%%\{\s*(?:init|initialize)\s*:([\s\S]*?)\}%%/gi;

function errorAt(code: string, message: string, line: number | null): DiagramError {
  return { stage: "input", code, message, line, column: null };
}

function lineOf(source: string, index: number): number {
  let line = 1;
  for (let position = 0; position < index && position < source.length; position += 1) {
    if (source.charCodeAt(position) === 10) {
      line += 1;
    }
  }
  return line;
}

/**
 * Cheap, deterministic checks applied before Mermaid parses anything. Returns null when the
 * source is acceptable.
 */
export function inspectSource(source: string, limits: Limits): DiagramError | null {
  if (source.trim().length === 0) {
    return errorAt("input.empty", "The Mermaid source is empty", null);
  }

  const bytes = Buffer.byteLength(source, "utf8");
  if (bytes > limits.maxInputBytes) {
    return errorAt(
      "input.too-large",
      `The Mermaid source is ${bytes} bytes, above the ${limits.maxInputBytes} byte limit`,
      null,
    );
  }

  const lines = source.split(/\r?\n/);
  if (lines.length > limits.maxInputLines) {
    return errorAt(
      "input.too-many-lines",
      `The Mermaid source has ${lines.length} lines, above the ${limits.maxInputLines} line limit`,
      null,
    );
  }

  INIT_DIRECTIVE.lastIndex = 0;
  for (let match = INIT_DIRECTIVE.exec(source); match !== null; match = INIT_DIRECTIVE.exec(source)) {
    const body = match[1] ?? "";
    for (const key of FORBIDDEN_DIRECTIVE_KEYS) {
      if (new RegExp(`["']?${key}["']?\\s*:`, "i").test(body)) {
        return errorAt(
          "input.unsafe-init-directive",
          `The init directive may not set "${key}"`,
          lineOf(source, match.index),
        );
      }
    }
  }

  for (const [index, line] of lines.entries()) {
    if (INTERACTION_STATEMENT.test(line)) {
      return errorAt(
        "input.interaction-directive",
        "Interaction statements (click/callback) are disabled",
        index + 1,
      );
    }
    const tag = DANGEROUS_TAG.exec(line);
    if (tag) {
      return errorAt("input.raw-html", `HTML tag "${tag[1]}" is not allowed in a diagram`, index + 1);
    }
    if (UNSAFE_URL.test(line)) {
      return errorAt("input.unsafe-url", "javascript:, vbscript: and data: URLs are not allowed", index + 1);
    }
  }

  return null;
}
