export const SCHEMA_VERSION = "1.0.0";
export const TOOL_NAME = "@quick-study/diagram-renderer";

export type FailureStage = "input" | "parse" | "render" | "sanitize";

export interface DiagramError {
  stage: FailureStage;
  /** Stable machine-readable code; see README for the closed vocabulary. */
  code: string;
  message: string;
  /** 1-based line inside the Mermaid source when the failure can be located. */
  line: number | null;
  column: number | null;
}

export interface RemovedItem {
  name: string;
  reason: string;
  count: number;
}

export interface SanitizationReport {
  removedElements: RemovedItem[];
  removedAttributes: RemovedItem[];
  modifiedStyleRules: number;
}

export interface RenderStats {
  inputBytes: number;
  inputLines: number;
  outputBytes: number;
  durationMs: number;
}

export interface ToolInfo {
  name: string;
  version: string;
  mermaid: string;
}

export interface Limits {
  maxInputBytes: number;
  maxInputLines: number;
  maxOutputBytes: number;
  renderTimeoutMs: number;
}

export interface RenderResult {
  schemaVersion: string;
  tool: ToolInfo;
  ok: boolean;
  diagramType: string | null;
  /** Sanitized SVG. Always null unless `ok` is true. */
  svg: string | null;
  error: DiagramError | null;
  sanitization: SanitizationReport;
  stats: RenderStats;
  limits: Limits;
}

export interface ValidationResult {
  ok: boolean;
  diagramType: string | null;
  error: DiagramError | null;
}

export interface RenderOptions {
  /** Deterministic id used for the SVG root and its scoped CSS. */
  id?: string;
  limits?: Partial<Limits>;
}

export const EMPTY_SANITIZATION: SanitizationReport = {
  removedElements: [],
  removedAttributes: [],
  modifiedStyleRules: 0,
};
