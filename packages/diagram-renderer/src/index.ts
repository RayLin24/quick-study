export { DiagramRendererError } from "./errors.ts";
export { inspectSource } from "./guard.ts";
export { DEFAULT_LIMITS, LIMIT_CEILINGS, resolveLimits } from "./limits.ts";
export { renderDiagram, validateDiagram } from "./renderer.ts";
export { sanitizeSvg, type SanitizeOutcome } from "./sanitize.ts";
export { getResultSchema } from "./schema.ts";
export {
  computeBoundingBox,
  parseTransformList,
  transformBox,
  type BoundingBoxOptions,
  type Box,
  type Matrix,
  type TextAnchor,
} from "./geometry.ts";
export { SCHEMA_VERSION, TOOL_NAME } from "./types.ts";
export type {
  DiagramError,
  FailureStage,
  Limits,
  RemovedItem,
  RenderOptions,
  RenderResult,
  RenderStats,
  SanitizationReport,
  ToolInfo,
  ValidationResult,
} from "./types.ts";

import { mermaidVersion } from "./version.ts";

/** Exact Mermaid release this package validates and renders with. */
export const MERMAID_VERSION: string = mermaidVersion();
