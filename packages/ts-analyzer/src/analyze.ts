import path from "node:path";

import ts from "typescript";

import { extractCallEdges } from "./calls.ts";
import { discoverSources, toPosix } from "./discovery.ts";
import { buildDependencies, extractImports } from "./imports.ts";
import { createDeadline, resolveLimits } from "./limits.ts";
import { createRestrictedProgram } from "./program.ts";
import { extractSymbols } from "./symbols.ts";
import { SCHEMA_VERSION, TOOL_NAME } from "./types.ts";
import type {
  AnalysisResult,
  AnalyzeOptions,
  AnalyzedFile,
  AnalyzerDiagnostic,
  CallEdge,
  ImportRecord,
  SymbolKind,
  SymbolRecord,
  SyntaxErrorInfo,
} from "./types.ts";
import { packageVersion } from "./version.ts";

const MAX_SYNTAX_ERRORS_PER_FILE = 20;

function syntaxErrorsOf(
  program: ts.Program,
  sourceFile: ts.SourceFile,
): SyntaxErrorInfo[] {
  return program
    .getSyntacticDiagnostics(sourceFile)
    .slice(0, MAX_SYNTAX_ERRORS_PER_FILE)
    .map((diagnostic) => {
      const position = diagnostic.start ?? 0;
      const { line, character } = sourceFile.getLineAndCharacterOfPosition(position);
      return {
        message: ts.flattenDiagnosticMessageText(diagnostic.messageText, " "),
        line: line + 1,
        column: character + 1,
        code: diagnostic.code,
      };
    });
}

export async function analyzeProject(options: AnalyzeOptions): Promise<AnalysisResult> {
  const startedAt = performance.now();
  const limits = resolveLimits(options.limits);
  const root = path.resolve(options.root);
  const entries = options.entries && options.entries.length > 0 ? options.entries : ["."];
  const deadline = createDeadline(limits.timeBudgetMs);

  const discovery = discoverSources(root, entries, limits, deadline);
  const diagnostics: AnalyzerDiagnostic[] = [...discovery.diagnostics];
  const truncationReasons = new Set(discovery.truncationReasons);

  const restricted = createRestrictedProgram(root, discovery.files);
  const files: AnalyzedFile[] = [];
  const symbols: SymbolRecord[] = [];
  const importRecords: ImportRecord[] = [];
  const callEdges: CallEdge[] = [];

  const declarationIds = new Map<ts.Node, string>();
  const kindById = new Map<string, SymbolKind>();
  const perFile: {
    sourceFile: ts.SourceFile;
    relativePath: string;
    imports: ReturnType<typeof extractImports>;
  }[] = [];

  for (const sourceFile of restricted.sourceFiles) {
    const relativePath = restricted.relativePathOf(sourceFile);
    if (relativePath === undefined) {
      continue;
    }
    const discovered = discovery.files.find((file) => file.relativePath === relativePath);
    if (!discovered) {
      continue;
    }

    const syntaxErrors = syntaxErrorsOf(restricted.program, sourceFile);
    if (syntaxErrors.length > 0) {
      diagnostics.push({
        severity: "warning",
        code: "parse.syntax-error",
        message: `${syntaxErrors.length} syntax error(s); symbols from this file may be incomplete`,
        path: relativePath,
      });
    }

    files.push({
      path: relativePath,
      language: discovered.language,
      bytes: discovered.bytes,
      lines: discovered.lines,
      sha256: discovered.sha256,
      syntaxErrors,
    });

    const extraction = extractSymbols(sourceFile, relativePath);
    symbols.push(...extraction.symbols);
    for (const [node, id] of extraction.declarationIds) {
      declarationIds.set(node, id);
    }
    for (const [id, kind] of extraction.kindById) {
      kindById.set(id, kind);
    }

    const imports = extractImports(restricted, sourceFile, relativePath);
    importRecords.push(...imports.records);
    perFile.push({ sourceFile, relativePath, imports });
  }

  // Call resolution needs every file's symbols, so it runs after the first pass.
  for (const entry of perFile) {
    if (deadline.expired()) {
      if (!truncationReasons.has("limit.time-budget")) {
        truncationReasons.add("limit.time-budget");
        diagnostics.push({
          severity: "warning",
          code: "limit.time-budget",
          message: "Stopped extracting call edges: the time budget expired",
          path: entry.relativePath,
        });
      }
      break;
    }
    callEdges.push(
      ...extractCallEdges(
        {
          program: restricted,
          checker: restricted.checker,
          declarationIds,
          kindById,
        },
        entry.sourceFile,
        entry.relativePath,
        entry.imports,
      ),
    );
  }

  symbols.sort(
    (a, b) =>
      a.file.localeCompare(b.file) ||
      a.range.startLine - b.range.startLine ||
      a.range.startColumn - b.range.startColumn,
  );
  importRecords.sort(
    (a, b) =>
      a.file.localeCompare(b.file) ||
      a.range.startLine - b.range.startLine ||
      a.range.startColumn - b.range.startColumn,
  );
  callEdges.sort(
    (a, b) =>
      a.fromFile.localeCompare(b.fromFile) ||
      a.range.startLine - b.range.startLine ||
      a.range.startColumn - b.range.startColumn,
  );
  diagnostics.sort(
    (a, b) => a.code.localeCompare(b.code) || (a.path ?? "").localeCompare(b.path ?? ""),
  );

  const dependencies = buildDependencies(importRecords);

  return {
    schemaVersion: SCHEMA_VERSION,
    tool: { name: TOOL_NAME, version: packageVersion(), typescript: ts.version },
    root: toPosix(root),
    files,
    symbols,
    imports: importRecords,
    dependencies,
    callEdges,
    diagnostics,
    limits: {
      applied: limits,
      truncated: truncationReasons.size > 0,
      truncationReasons: [...truncationReasons].sort(),
    },
    stats: {
      fileCount: files.length,
      totalBytes: files.reduce((total, file) => total + file.bytes, 0),
      symbolCount: symbols.length,
      importCount: importRecords.length,
      dependencyCount: dependencies.length,
      callEdgeCount: callEdges.length,
      resolvedCallEdgeCount: callEdges.filter((edge) => edge.resolution === "resolved").length,
      externalCallEdgeCount: callEdges.filter((edge) => edge.resolution === "external").length,
      unresolvedCallEdgeCount: callEdges.filter((edge) => edge.resolution === "unresolved").length,
    },
    timing: { durationMs: Math.round(performance.now() - startedAt) },
  };
}
