import ts from "typescript";

import { isRequireCall } from "./imports.ts";
import type { RestrictedProgram } from "./program.ts";
import { rangeOf } from "./symbols.ts";
import type { CallEdge, CallKind, Confidence, CallResolution, ImportRecord, SymbolKind } from "./types.ts";

const MAX_CALLEE_TEXT_LENGTH = 120;

/** Symbol kinds whose call target can still be replaced at runtime by a subclass. */
const OVERRIDABLE_KINDS: ReadonlySet<SymbolKind> = new Set<SymbolKind>([
  "method",
  "getter",
  "setter",
  "property",
]);

export interface CallExtractionContext {
  program: RestrictedProgram;
  checker: ts.TypeChecker;
  declarationIds: Map<ts.Node, string>;
  kindById: Map<string, SymbolKind>;
}

interface Outcome {
  to: string | null;
  resolvedFile: string | null;
  resolution: CallResolution;
  confidence: Confidence;
  reason: string;
  callKind: CallKind;
  externalModule: string | null;
}

function truncate(value: string): string {
  const collapsed = value.replace(/\s+/g, " ").trim();
  return collapsed.length > MAX_CALLEE_TEXT_LENGTH
    ? `${collapsed.slice(0, MAX_CALLEE_TEXT_LENGTH - 3)}...`
    : collapsed;
}

function calleeNameOf(expression: ts.Expression): string | null {
  if (ts.isIdentifier(expression)) {
    return expression.text;
  }
  if (ts.isPropertyAccessExpression(expression)) {
    return expression.name.text;
  }
  if (ts.isElementAccessExpression(expression)) {
    const argument = expression.argumentExpression;
    return ts.isStringLiteralLike(argument) ? argument.text : null;
  }
  return null;
}

/** Leftmost identifier of a property/element access chain, e.g. `chalk` in `chalk.bold.red`. */
function leftmostIdentifier(expression: ts.Expression): ts.Identifier | null {
  let current: ts.Expression = expression;
  for (;;) {
    if (ts.isIdentifier(current)) {
      return current;
    }
    if (ts.isPropertyAccessExpression(current) || ts.isElementAccessExpression(current)) {
      current = current.expression;
      continue;
    }
    return null;
  }
}

function unresolved(reason: string, callKind: CallKind): Outcome {
  return {
    to: null,
    resolvedFile: null,
    resolution: "unresolved",
    confidence: "low",
    reason,
    callKind,
    externalModule: null,
  };
}

function fromImportRecord(record: ImportRecord, callKind: CallKind, reason: string): Outcome {
  if (record.resolution === "internal" && record.resolvedFile !== null) {
    return {
      to: null,
      resolvedFile: record.resolvedFile,
      resolution: "resolved",
      confidence: "high",
      reason,
      callKind,
      externalModule: null,
    };
  }
  if (record.resolution === "external") {
    return {
      to: null,
      resolvedFile: null,
      resolution: "external",
      confidence: "medium",
      reason: "external-module",
      callKind,
      externalModule: record.moduleSpecifier,
    };
  }
  return unresolved(
    callKind === "require" ? "require-non-literal" : "dynamic-import-non-literal",
    callKind,
  );
}

export function extractCallEdges(
  context: CallExtractionContext,
  sourceFile: ts.SourceFile,
  file: string,
  imports: { callRecords: Map<ts.CallExpression, ImportRecord>; bindingImports: Map<ts.Node, ImportRecord> },
): CallEdge[] {
  const { checker, declarationIds, kindById, program } = context;
  const edges: CallEdge[] = [];
  const idCounters = new Map<string, number>();

  const enclosingSymbolId = (node: ts.Node): string | null => {
    let current: ts.Node | undefined = node.parent;
    while (current) {
      const id = declarationIds.get(current);
      if (id !== undefined) {
        return id;
      }
      current = current.parent;
    }
    return null;
  };

  const importRecordForDeclaration = (declaration: ts.Node): ImportRecord | undefined =>
    imports.bindingImports.get(declaration);

  const resolveThroughChecker = (expression: ts.Expression, callKind: CallKind): Outcome => {
    let symbol = checker.getSymbolAtLocation(expression);

    if (symbol && symbol.flags & ts.SymbolFlags.Alias) {
      try {
        symbol = checker.getAliasedSymbol(symbol);
      } catch {
        // An alias to an unresolvable module stays unaliased.
      }
    }

    const declarations = symbol?.declarations ?? [];
    if (declarations.length === 0) {
      if (
        ts.isPropertyAccessExpression(expression) &&
        checker.getTypeAtLocation(expression.expression).flags & ts.TypeFlags.Any
      ) {
        return unresolved("callee-type-any", callKind);
      }
      if (checker.getTypeAtLocation(expression).flags & ts.TypeFlags.Any) {
        return unresolved("callee-type-any", callKind);
      }
      return unresolved("no-declaration-for-callee", callKind);
    }

    const targets = new Set<string>();
    for (const declaration of declarations) {
      const id = declarationIds.get(declaration);
      if (id !== undefined) {
        targets.add(id);
      }
    }

    if (targets.size === 1) {
      const [target] = [...targets];
      const kind = target === undefined ? undefined : kindById.get(target);
      return {
        to: target ?? null,
        resolvedFile: null,
        resolution: "resolved",
        confidence: kind && OVERRIDABLE_KINDS.has(kind) ? "medium" : "high",
        reason: "checker-unique-declaration",
        callKind,
        externalModule: null,
      };
    }
    if (targets.size > 1) {
      return {
        to: null,
        resolvedFile: null,
        resolution: "ambiguous",
        confidence: "low",
        reason: "checker-multiple-declarations",
        callKind,
        externalModule: null,
      };
    }

    if (declarations.some((declaration) => ts.isParameter(declaration))) {
      return unresolved("parameter-invocation", callKind);
    }
    if (
      declarations.some((declaration) =>
        program.isInAnalysisSet(declaration.getSourceFile().fileName),
      )
    ) {
      return unresolved("local-binding", callKind);
    }
    return {
      to: null,
      resolvedFile: null,
      resolution: "external",
      confidence: "medium",
      reason: "declaration-outside-analysis-set",
      callKind,
      externalModule: null,
    };
  };

  const declaredInAnalysisSet = (expression: ts.Expression): boolean => {
    const symbol = checker.getSymbolAtLocation(expression);
    return (symbol?.declarations ?? []).some((declaration) =>
      program.isInAnalysisSet(declaration.getSourceFile().fileName),
    );
  };

  const resolve = (node: ts.CallExpression | ts.NewExpression): Outcome => {
    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const record = imports.callRecords.get(node);
      return record
        ? fromImportRecord(record, "dynamic-import", "import-literal")
        : unresolved("dynamic-import-non-literal", "dynamic-import");
    }

    if (ts.isCallExpression(node) && isRequireCall(node)) {
      const record = imports.callRecords.get(node);
      return record
        ? fromImportRecord(record, "require", "require-literal")
        : unresolved("require-non-literal", "require");
    }

    const expression = node.expression;
    const isConstructor = ts.isNewExpression(node);

    // Reflection entry points are never treated as a known target.
    if (
      ts.isIdentifier(expression) &&
      (expression.text === "eval" || expression.text === "Function") &&
      !declaredInAnalysisSet(expression)
    ) {
      return unresolved("dynamic-eval", isConstructor ? "constructor" : "unknown");
    }

    if (ts.isElementAccessExpression(expression) && !ts.isStringLiteralLike(expression.argumentExpression)) {
      return unresolved("computed-member-access", "computed");
    }

    const callKind: CallKind = isConstructor
      ? "constructor"
      : ts.isPropertyAccessExpression(expression) || ts.isElementAccessExpression(expression)
        ? "method"
        : "function";

    const root = leftmostIdentifier(expression);
    if (root) {
      const symbol = checker.getSymbolAtLocation(root);
      for (const declaration of symbol?.declarations ?? []) {
        const record = importRecordForDeclaration(declaration);
        if (record && record.resolution === "external") {
          return {
            to: null,
            resolvedFile: null,
            resolution: "external",
            confidence: "medium",
            reason: "external-module",
            callKind,
            externalModule: record.moduleSpecifier,
          };
        }
      }
    }

    return resolveThroughChecker(expression, callKind);
  };

  const visit = (node: ts.Node): void => {
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const outcome = resolve(node);
      const range = rangeOf(node, sourceFile);
      const positionKey = `${range.startLine}:${range.startColumn}`;
      const ordinal = (idCounters.get(positionKey) ?? 0) + 1;
      idCounters.set(positionKey, ordinal);

      edges.push({
        id: `${file}:${positionKey}#${ordinal}`,
        from: enclosingSymbolId(node),
        fromFile: file,
        to: outcome.to,
        resolvedFile: outcome.resolvedFile,
        calleeText: truncate(node.expression.getText(sourceFile)),
        calleeName: calleeNameOf(node.expression),
        resolution: outcome.resolution,
        confidence: outcome.confidence,
        reason: outcome.reason,
        callKind: outcome.callKind,
        externalModule: outcome.externalModule,
        range,
      });
    }
    ts.forEachChild(node, visit);
  };

  visit(sourceFile);

  edges.sort(
    (a, b) => a.range.startLine - b.range.startLine || a.range.startColumn - b.range.startColumn,
  );
  return edges;
}
