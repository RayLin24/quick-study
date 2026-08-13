export const SCHEMA_VERSION = "1.0.0";
export const TOOL_NAME = "@quick-study/ts-analyzer";

export type Language = "ts" | "tsx" | "mts" | "cts" | "js" | "jsx" | "mjs" | "cjs";

export type SymbolKind =
  | "function"
  | "class"
  | "method"
  | "constructor"
  | "getter"
  | "setter"
  | "property"
  | "variable"
  | "interface"
  | "type-alias"
  | "enum"
  | "namespace";

export type ExportKind = "named" | "default";

export type ImportKind = "static" | "dynamic" | "require" | "re-export";
export type ImportResolution = "internal" | "external" | "unresolved";
export type BindingKind = "named" | "default" | "namespace";

export type CallResolution = "resolved" | "external" | "ambiguous" | "unresolved";
export type Confidence = "high" | "medium" | "low";
export type CallKind =
  | "function"
  | "method"
  | "constructor"
  | "dynamic-import"
  | "require"
  | "computed"
  | "unknown";

export type DiagnosticSeverity = "info" | "warning" | "error";

export interface SourceRange {
  /** 1-based inclusive line of the first character. */
  startLine: number;
  /** 1-based inclusive column of the first character. */
  startColumn: number;
  endLine: number;
  endColumn: number;
}

export interface SyntaxErrorInfo {
  message: string;
  line: number;
  column: number;
  code: number;
}

export interface AnalyzedFile {
  /** POSIX path relative to the analysis root. */
  path: string;
  language: Language;
  bytes: number;
  lines: number;
  sha256: string;
  syntaxErrors: SyntaxErrorInfo[];
}

export interface SymbolRecord {
  /** `<file>#<qualifiedName>`, suffixed with `~n` when a name is declared more than once. */
  id: string;
  name: string;
  qualifiedName: string;
  kind: SymbolKind;
  file: string;
  range: SourceRange;
  exported: boolean;
  exportKind: ExportKind | null;
  exportName: string | null;
  isAsync: boolean;
  isGenerator: boolean;
  isStatic: boolean;
  isAbstract: boolean;
  parentId: string | null;
  signature: string | null;
  docSummary: string | null;
}

export interface ImportBinding {
  /** Name in the source module, `*` for namespace imports, `default` for default imports. */
  imported: string;
  local: string;
  kind: BindingKind;
}

export interface ImportRecord {
  file: string;
  moduleSpecifier: string;
  kind: ImportKind;
  typeOnly: boolean;
  resolution: ImportResolution;
  /** Root-relative path, only set when `resolution` is `internal`. */
  resolvedFile: string | null;
  unresolvedReason: string | null;
  bindings: ImportBinding[];
  range: SourceRange;
}

export interface DependencyEdge {
  from: string;
  /** Root-relative file path for internal edges, package specifier for external edges. */
  to: string;
  scope: "internal" | "external";
  count: number;
}

export interface CallEdge {
  id: string;
  /** Symbol id of the caller, or null for calls at module top level. */
  from: string | null;
  fromFile: string;
  /** Symbol id of the callee. Only ever set when `resolution` is `resolved`. */
  to: string | null;
  /** Root-relative target file for `dynamic-import` and `require` edges. */
  resolvedFile: string | null;
  calleeText: string;
  calleeName: string | null;
  resolution: CallResolution;
  confidence: Confidence;
  /** Machine-readable justification; see README for the closed vocabulary. */
  reason: string;
  callKind: CallKind;
  externalModule: string | null;
  range: SourceRange;
}

export interface AnalyzerDiagnostic {
  severity: DiagnosticSeverity;
  code: string;
  message: string;
  path: string | null;
}

export interface Limits {
  maxFiles: number;
  maxFileBytes: number;
  maxTotalBytes: number;
  maxDirectoryDepth: number;
  timeBudgetMs: number;
}

export interface LimitReport {
  applied: Limits;
  truncated: boolean;
  truncationReasons: string[];
}

export interface AnalysisStats {
  fileCount: number;
  totalBytes: number;
  symbolCount: number;
  importCount: number;
  dependencyCount: number;
  callEdgeCount: number;
  resolvedCallEdgeCount: number;
  externalCallEdgeCount: number;
  unresolvedCallEdgeCount: number;
}

export interface ToolInfo {
  name: string;
  version: string;
  typescript: string;
}

export interface AnalysisResult {
  schemaVersion: string;
  tool: ToolInfo;
  /** Absolute path of the analysis root; every other path is relative to it. */
  root: string;
  files: AnalyzedFile[];
  symbols: SymbolRecord[];
  imports: ImportRecord[];
  dependencies: DependencyEdge[];
  callEdges: CallEdge[];
  diagnostics: AnalyzerDiagnostic[];
  limits: LimitReport;
  stats: AnalysisStats;
  /** The only environment-dependent part of the document. */
  timing: { durationMs: number };
}

export interface AnalyzeOptions {
  root: string;
  /** Files or directories relative to `root`. Defaults to the root itself. */
  entries?: string[];
  limits?: Partial<Limits>;
}
