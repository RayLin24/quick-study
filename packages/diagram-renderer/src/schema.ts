import { SCHEMA_VERSION } from "./types.ts";

type JsonSchema = Record<string, unknown>;

/**
 * JSON Schema (draft 2020-12) for the render result. Consumers such as the diagram quality gate
 * validate against it, so a breaking change requires a new `SCHEMA_VERSION`.
 */
export function getResultSchema(): JsonSchema {
  return {
    $schema: "https://json-schema.org/draft/2020-12/schema",
    $id: `https://quick-study.local/schemas/diagram-renderer/${SCHEMA_VERSION}/result.json`,
    title: "Quick Study diagram render result",
    type: "object",
    additionalProperties: false,
    required: [
      "schemaVersion",
      "tool",
      "ok",
      "diagramType",
      "svg",
      "error",
      "sanitization",
      "stats",
      "limits",
    ],
    properties: {
      schemaVersion: { type: "string", const: SCHEMA_VERSION },
      tool: {
        type: "object",
        additionalProperties: false,
        required: ["name", "version", "mermaid"],
        properties: {
          name: { type: "string" },
          version: { type: "string" },
          mermaid: { type: "string", description: "Exact Mermaid release used for parse and render." },
        },
      },
      ok: { type: "boolean" },
      diagramType: {
        type: ["string", "null"],
        description: "Mermaid diagram type, for example flowchart-v2, sequence or class.",
      },
      svg: {
        type: ["string", "null"],
        description: "Sanitized SVG. Null whenever ok is false; a broken diagram is never emitted.",
      },
      error: {
        oneOf: [{ type: "null" }, { $ref: "#/$defs/error" }],
      },
      sanitization: { $ref: "#/$defs/sanitization" },
      stats: { $ref: "#/$defs/stats" },
      limits: { $ref: "#/$defs/limits" },
    },
    $defs: {
      stage: {
        type: "string",
        enum: ["input", "parse", "render", "sanitize"],
        description:
          "input: rejected before Mermaid saw it. parse: mermaid.parse() failed. render: rendering failed or exceeded a limit. sanitize: the SVG could not be made safe.",
      },
      error: {
        type: "object",
        additionalProperties: false,
        required: ["stage", "code", "message", "line", "column"],
        properties: {
          stage: { $ref: "#/$defs/stage" },
          code: {
            type: "string",
            enum: [
              "input.empty",
              "input.too-large",
              "input.too-many-lines",
              "input.interaction-directive",
              "input.raw-html",
              "input.unsafe-url",
              "input.unsafe-init-directive",
              "parse.syntax-error",
              "parse.unknown-diagram-type",
              "render.failed",
              "render.timeout",
              "render.output-too-large",
              "sanitize.no-svg-root",
              "sanitize.empty-output",
            ],
          },
          message: { type: "string" },
          line: { type: ["integer", "null"], minimum: 1 },
          column: { type: ["integer", "null"], minimum: 1 },
        },
      },
      removedItem: {
        type: "object",
        additionalProperties: false,
        required: ["name", "reason", "count"],
        properties: {
          name: { type: "string" },
          reason: {
            type: "string",
            enum: ["forbidden-element", "foreign-namespace", "event-handler", "external-reference", "unsafe-value"],
          },
          count: { type: "integer", minimum: 1 },
        },
      },
      sanitization: {
        type: "object",
        additionalProperties: false,
        required: ["removedElements", "removedAttributes", "modifiedStyleRules"],
        properties: {
          removedElements: { type: "array", items: { $ref: "#/$defs/removedItem" } },
          removedAttributes: { type: "array", items: { $ref: "#/$defs/removedItem" } },
          modifiedStyleRules: { type: "integer", minimum: 0 },
        },
      },
      stats: {
        type: "object",
        additionalProperties: false,
        required: ["inputBytes", "inputLines", "outputBytes", "durationMs"],
        properties: {
          inputBytes: { type: "integer", minimum: 0 },
          inputLines: { type: "integer", minimum: 0 },
          outputBytes: { type: "integer", minimum: 0 },
          durationMs: {
            type: "integer",
            minimum: 0,
            description: "The only non-deterministic field in the document.",
          },
        },
      },
      limits: {
        type: "object",
        additionalProperties: false,
        required: ["maxInputBytes", "maxInputLines", "maxOutputBytes", "renderTimeoutMs"],
        properties: {
          maxInputBytes: { type: "integer", minimum: 0 },
          maxInputLines: { type: "integer", minimum: 0 },
          maxOutputBytes: { type: "integer", minimum: 0 },
          renderTimeoutMs: { type: "integer", minimum: 0 },
        },
      },
    },
  };
}
