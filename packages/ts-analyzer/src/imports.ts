import ts from "typescript";

import { resolveToAnalysisSet, type RestrictedProgram } from "./program.ts";
import { rangeOf } from "./symbols.ts";
import type { DependencyEdge, ImportBinding, ImportRecord, ImportResolution } from "./types.ts";

const MAX_SPECIFIER_LENGTH = 200;

export function packageRootOf(specifier: string): string {
  if (specifier.startsWith("node:")) {
    return specifier;
  }
  if (specifier.startsWith("@")) {
    const parts = specifier.split("/");
    return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : specifier;
  }
  return specifier.split("/")[0] ?? specifier;
}

export function isRequireCall(node: ts.Node): node is ts.CallExpression {
  return (
    ts.isCallExpression(node) &&
    ts.isIdentifier(node.expression) &&
    node.expression.text === "require" &&
    node.arguments.length === 1
  );
}

interface Classification {
  resolution: ImportResolution;
  resolvedFile: string | null;
  unresolvedReason: string | null;
}

function classify(
  program: RestrictedProgram,
  sourceFile: ts.SourceFile,
  specifier: string,
): Classification {
  const resolvedFile = resolveToAnalysisSet(program, sourceFile, specifier);
  if (resolvedFile !== null) {
    return { resolution: "internal", resolvedFile, unresolvedReason: null };
  }
  const isRelative = specifier.startsWith(".") || specifier.startsWith("/");
  if (isRelative) {
    return {
      resolution: "unresolved",
      resolvedFile: null,
      unresolvedReason: "module-not-found",
    };
  }
  return { resolution: "external", resolvedFile: null, unresolvedReason: null };
}

function bindingsOfImportClause(clause: ts.ImportClause | undefined): ImportBinding[] {
  const bindings: ImportBinding[] = [];
  if (!clause) {
    return bindings;
  }
  if (clause.name) {
    bindings.push({ imported: "default", local: clause.name.text, kind: "default" });
  }
  const named = clause.namedBindings;
  if (named && ts.isNamespaceImport(named)) {
    bindings.push({ imported: "*", local: named.name.text, kind: "namespace" });
  } else if (named && ts.isNamedImports(named)) {
    for (const element of named.elements) {
      const imported = (element.propertyName ?? element.name).text;
      bindings.push({
        imported,
        local: element.name.text,
        kind: imported === "default" ? "default" : "named",
      });
    }
  }
  return bindings;
}

export interface ImportExtraction {
  records: ImportRecord[];
  /** Records keyed by the `require()` / `import()` call that produced them. */
  callRecords: Map<ts.CallExpression, ImportRecord>;
  /** Local binding declarations keyed to the import that introduced them. */
  bindingImports: Map<ts.Node, ImportRecord>;
}

export function extractImports(
  program: RestrictedProgram,
  sourceFile: ts.SourceFile,
  file: string,
): ImportExtraction {
  const records: ImportRecord[] = [];
  const callRecords = new Map<ts.CallExpression, ImportRecord>();
  const bindingImports = new Map<ts.Node, ImportRecord>();

  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      const specifier = node.moduleSpecifier.text;
      const clause = node.importClause;
      const record: ImportRecord = {
        file,
        moduleSpecifier: specifier,
        kind: "static",
        typeOnly: clause?.isTypeOnly ?? false,
        ...classify(program, sourceFile, specifier),
        bindings: bindingsOfImportClause(clause),
        range: rangeOf(node, sourceFile),
      };
      records.push(record);
      if (clause?.name) {
        // A default import's alias symbol is declared by the clause itself, not by its name.
        bindingImports.set(clause, record);
        bindingImports.set(clause.name, record);
      }
      const named = clause?.namedBindings;
      if (named && ts.isNamespaceImport(named)) {
        bindingImports.set(named, record);
        bindingImports.set(named.name, record);
      } else if (named && ts.isNamedImports(named)) {
        for (const element of named.elements) {
          bindingImports.set(element, record);
          bindingImports.set(element.name, record);
        }
      }
      return;
    }

    if (ts.isExportDeclaration(node) && node.moduleSpecifier && ts.isStringLiteral(node.moduleSpecifier)) {
      const specifier = node.moduleSpecifier.text;
      const bindings: ImportBinding[] = [];
      const clause = node.exportClause;
      if (clause && ts.isNamedExports(clause)) {
        for (const element of clause.elements) {
          bindings.push({
            imported: (element.propertyName ?? element.name).text,
            local: element.name.text,
            kind: "named",
          });
        }
      } else if (clause && ts.isNamespaceExport(clause)) {
        bindings.push({ imported: "*", local: clause.name.text, kind: "namespace" });
      } else {
        bindings.push({ imported: "*", local: "*", kind: "namespace" });
      }
      records.push({
        file,
        moduleSpecifier: specifier,
        kind: "re-export",
        typeOnly: node.isTypeOnly,
        ...classify(program, sourceFile, specifier),
        bindings,
        range: rangeOf(node, sourceFile),
      });
      return;
    }

    if (ts.isCallExpression(node) && node.expression.kind === ts.SyntaxKind.ImportKeyword) {
      const argument = node.arguments[0];
      const literal = argument && ts.isStringLiteralLike(argument) ? argument.text : null;
      const record: ImportRecord =
        literal === null
          ? {
              file,
              moduleSpecifier: truncate(argument ? argument.getText(sourceFile) : ""),
              kind: "dynamic",
              typeOnly: false,
              resolution: "unresolved",
              resolvedFile: null,
              unresolvedReason: "dynamic-specifier",
              bindings: [],
              range: rangeOf(node, sourceFile),
            }
          : {
              file,
              moduleSpecifier: literal,
              kind: "dynamic",
              typeOnly: false,
              ...classify(program, sourceFile, literal),
              bindings: [],
              range: rangeOf(node, sourceFile),
            };
      records.push(record);
      callRecords.set(node, record);
      ts.forEachChild(node, visit);
      return;
    }

    if (isRequireCall(node)) {
      const argument = node.arguments[0];
      const literal = argument && ts.isStringLiteralLike(argument) ? argument.text : null;
      const record: ImportRecord =
        literal === null
          ? {
              file,
              moduleSpecifier: truncate(argument ? argument.getText(sourceFile) : ""),
              kind: "require",
              typeOnly: false,
              resolution: "unresolved",
              resolvedFile: null,
              unresolvedReason: "dynamic-specifier",
              bindings: [],
              range: rangeOf(node, sourceFile),
            }
          : {
              file,
              moduleSpecifier: literal,
              kind: "require",
              typeOnly: false,
              ...classify(program, sourceFile, literal),
              bindings: [],
              range: rangeOf(node, sourceFile),
            };
      records.push(record);
      callRecords.set(node, record);

      // `const x = require("pkg")` makes `x` behave like a default import binding.
      const parent = node.parent;
      if (parent && ts.isVariableDeclaration(parent) && ts.isIdentifier(parent.name)) {
        bindingImports.set(parent, record);
        bindingImports.set(parent.name, record);
      }
      ts.forEachChild(node, visit);
      return;
    }

    ts.forEachChild(node, visit);
  };

  visit(sourceFile);
  return { records, callRecords, bindingImports };
}

function truncate(value: string): string {
  const collapsed = value.replace(/\s+/g, " ").trim();
  return collapsed.length > MAX_SPECIFIER_LENGTH
    ? `${collapsed.slice(0, MAX_SPECIFIER_LENGTH - 3)}...`
    : collapsed;
}

export function buildDependencies(records: ImportRecord[]): DependencyEdge[] {
  const counts = new Map<string, DependencyEdge>();

  for (const record of records) {
    let edge: DependencyEdge | null = null;
    if (record.resolution === "internal" && record.resolvedFile !== null) {
      edge = { from: record.file, to: record.resolvedFile, scope: "internal", count: 0 };
    } else if (record.resolution === "external") {
      edge = {
        from: record.file,
        to: packageRootOf(record.moduleSpecifier),
        scope: "external",
        count: 0,
      };
    }
    if (!edge) {
      continue;
    }
    const key = `${edge.scope}\u0000${edge.from}\u0000${edge.to}`;
    const existing = counts.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      edge.count = 1;
      counts.set(key, edge);
    }
  }

  return [...counts.values()].sort(
    (a, b) =>
      a.scope.localeCompare(b.scope) || a.from.localeCompare(b.from) || a.to.localeCompare(b.to),
  );
}
