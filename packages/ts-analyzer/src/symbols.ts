import ts from "typescript";

import type { ExportKind, SourceRange, SymbolKind, SymbolRecord } from "./types.ts";

const MAX_SIGNATURE_LENGTH = 200;

export function rangeOf(node: ts.Node, sourceFile: ts.SourceFile): SourceRange {
  const start = sourceFile.getLineAndCharacterOfPosition(node.getStart(sourceFile));
  const end = sourceFile.getLineAndCharacterOfPosition(node.getEnd());
  return {
    startLine: start.line + 1,
    startColumn: start.character + 1,
    endLine: end.line + 1,
    endColumn: end.character + 1,
  };
}

function hasModifier(node: ts.Node, kind: ts.SyntaxKind): boolean {
  if (!ts.canHaveModifiers(node)) {
    return false;
  }
  return ts.getModifiers(node)?.some((modifier) => modifier.kind === kind) ?? false;
}

function docSummaryOf(node: ts.Node): string | null {
  for (const doc of ts.getJSDocCommentsAndTags(node)) {
    if (!ts.isJSDoc(doc)) {
      continue;
    }
    const comment = typeof doc.comment === "string" ? doc.comment : ts.getTextOfJSDocComment(doc.comment);
    const text = comment?.replace(/\s+/g, " ").trim();
    if (text) {
      return text;
    }
  }
  return null;
}

function bodyStartOf(node: ts.Node, sourceFile: ts.SourceFile): number | null {
  if (
    ts.isFunctionDeclaration(node) ||
    ts.isMethodDeclaration(node) ||
    ts.isConstructorDeclaration(node) ||
    ts.isGetAccessorDeclaration(node) ||
    ts.isSetAccessorDeclaration(node) ||
    ts.isFunctionExpression(node) ||
    ts.isArrowFunction(node)
  ) {
    return node.body ? node.body.getStart(sourceFile) : null;
  }
  return null;
}

function signatureOf(node: ts.Node, sourceFile: ts.SourceFile): string | null {
  const start = node.getStart(sourceFile);
  let end = node.getEnd();
  const bodyStart = bodyStartOf(node, sourceFile);

  if (bodyStart !== null) {
    end = bodyStart;
  } else if (ts.isClassLike(node) || ts.isInterfaceDeclaration(node) || ts.isEnumDeclaration(node)) {
    const brace = sourceFile.text.indexOf("{", start);
    end = brace > start ? brace : end;
  } else if (ts.isVariableDeclaration(node)) {
    const initializer = node.initializer;
    if (initializer && (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer))) {
      end = initializer.body.getStart(sourceFile);
    } else {
      end = (node.type ?? node.name).getEnd();
    }
  }

  const text = sourceFile.text.slice(start, Math.max(start, end)).replace(/\s+/g, " ").trim();
  if (text.length === 0) {
    return null;
  }
  return text.length > MAX_SIGNATURE_LENGTH ? `${text.slice(0, MAX_SIGNATURE_LENGTH - 3)}...` : text;
}

interface FileExports {
  named: Map<string, string>;
  defaultLocal: string | null;
}

function collectFileExports(sourceFile: ts.SourceFile): FileExports {
  const named = new Map<string, string>();
  let defaultLocal: string | null = null;

  for (const statement of sourceFile.statements) {
    if (ts.isExportDeclaration(statement) && !statement.moduleSpecifier && statement.exportClause) {
      if (ts.isNamedExports(statement.exportClause)) {
        for (const element of statement.exportClause.elements) {
          named.set((element.propertyName ?? element.name).text, element.name.text);
        }
      }
      continue;
    }
    if (ts.isExportAssignment(statement) && ts.isIdentifier(statement.expression)) {
      defaultLocal = statement.expression.text;
    }
  }

  return { named, defaultLocal };
}

export interface SymbolExtraction {
  symbols: SymbolRecord[];
  /** Declaration node to symbol id, used to resolve call targets. */
  declarationIds: Map<ts.Node, string>;
  kindById: Map<string, SymbolKind>;
}

export function extractSymbols(sourceFile: ts.SourceFile, file: string): SymbolExtraction {
  const symbols: SymbolRecord[] = [];
  const declarationIds = new Map<ts.Node, string>();
  const kindById = new Map<string, SymbolKind>();
  const usedIds = new Map<string, number>();
  const fileExports = collectFileExports(sourceFile);

  const makeId = (qualifiedName: string): string => {
    const base = `${file}#${qualifiedName}`;
    const seen = usedIds.get(base) ?? 0;
    usedIds.set(base, seen + 1);
    return seen === 0 ? base : `${base}~${seen + 1}`;
  };

  const add = (
    node: ts.Node,
    name: string,
    qualifiedName: string,
    kind: SymbolKind,
    parentId: string | null,
    isMember: boolean,
  ): string => {
    const hasExportModifier = hasModifier(node, ts.SyntaxKind.ExportKeyword);
    const hasDefaultModifier = hasModifier(node, ts.SyntaxKind.DefaultKeyword);
    const namedExportAs = fileExports.named.get(name);
    const isDefaultAssignment = fileExports.defaultLocal === name;

    let exportKind: ExportKind | null = null;
    let exportName: string | null = null;
    if (!isMember) {
      if (hasExportModifier && hasDefaultModifier) {
        exportKind = "default";
        exportName = "default";
      } else if (isDefaultAssignment) {
        exportKind = "default";
        exportName = "default";
      } else if (hasExportModifier) {
        exportKind = "named";
        exportName = name;
      } else if (namedExportAs !== undefined) {
        exportKind = "named";
        exportName = namedExportAs;
      }
    }

    const id = makeId(qualifiedName);
    symbols.push({
      id,
      name,
      qualifiedName,
      kind,
      file,
      range: rangeOf(node, sourceFile),
      exported: exportKind !== null,
      exportKind,
      exportName,
      isAsync: hasModifier(node, ts.SyntaxKind.AsyncKeyword),
      isGenerator:
        (ts.isFunctionDeclaration(node) || ts.isMethodDeclaration(node)) &&
        node.asteriskToken !== undefined,
      isStatic: hasModifier(node, ts.SyntaxKind.StaticKeyword),
      isAbstract: hasModifier(node, ts.SyntaxKind.AbstractKeyword),
      parentId,
      signature: signatureOf(node, sourceFile),
      docSummary: docSummaryOf(node),
    });
    declarationIds.set(node, id);
    kindById.set(id, kind);
    return id;
  };

  const addClassMembers = (node: ts.ClassLikeDeclaration, className: string, classId: string): void => {
    for (const member of node.members) {
      if (ts.isConstructorDeclaration(member)) {
        add(member, "constructor", `${className}.constructor`, "constructor", classId, true);
        continue;
      }
      const memberName = member.name;
      if (!memberName || !(ts.isIdentifier(memberName) || ts.isStringLiteral(memberName))) {
        continue;
      }
      const name = memberName.text;
      const qualified = `${className}.${name}`;
      if (ts.isMethodDeclaration(member)) {
        add(member, name, qualified, "method", classId, true);
      } else if (ts.isGetAccessorDeclaration(member)) {
        add(member, name, qualified, "getter", classId, true);
      } else if (ts.isSetAccessorDeclaration(member)) {
        add(member, name, qualified, "setter", classId, true);
      } else if (ts.isPropertyDeclaration(member)) {
        const initializer = member.initializer;
        const kind: SymbolKind =
          initializer && (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer))
            ? "method"
            : "property";
        add(member, name, qualified, kind, classId, true);
      }
    }
  };

  const visitStatement = (statement: ts.Statement, prefix: string, parentId: string | null): void => {
    if (ts.isFunctionDeclaration(statement) && statement.name) {
      add(statement, statement.name.text, `${prefix}${statement.name.text}`, "function", parentId, false);
      return;
    }
    if (ts.isClassDeclaration(statement) && statement.name) {
      const name = statement.name.text;
      const id = add(statement, name, `${prefix}${name}`, "class", parentId, false);
      addClassMembers(statement, `${prefix}${name}`, id);
      return;
    }
    if (ts.isInterfaceDeclaration(statement)) {
      add(statement, statement.name.text, `${prefix}${statement.name.text}`, "interface", parentId, false);
      return;
    }
    if (ts.isTypeAliasDeclaration(statement)) {
      add(statement, statement.name.text, `${prefix}${statement.name.text}`, "type-alias", parentId, false);
      return;
    }
    if (ts.isEnumDeclaration(statement)) {
      add(statement, statement.name.text, `${prefix}${statement.name.text}`, "enum", parentId, false);
      return;
    }
    if (ts.isModuleDeclaration(statement) && ts.isIdentifier(statement.name)) {
      const name = statement.name.text;
      const id = add(statement, name, `${prefix}${name}`, "namespace", parentId, false);
      const body = statement.body;
      if (body && ts.isModuleBlock(body)) {
        for (const inner of body.statements) {
          visitStatement(inner, `${prefix}${name}.`, id);
        }
      }
      return;
    }
    if (ts.isVariableStatement(statement)) {
      const exportedStatement = hasModifier(statement, ts.SyntaxKind.ExportKeyword);
      for (const declaration of statement.declarationList.declarations) {
        if (!ts.isIdentifier(declaration.name)) {
          continue;
        }
        const initializer = declaration.initializer;
        const kind: SymbolKind =
          initializer && (ts.isArrowFunction(initializer) || ts.isFunctionExpression(initializer))
            ? "function"
            : "variable";
        const name = declaration.name.text;
        // The `export` keyword lives on the statement, so borrow it for the declaration.
        const exportsNamed = fileExports.named;
        if (exportedStatement && !exportsNamed.has(name)) {
          exportsNamed.set(name, name);
        }
        add(declaration, name, `${prefix}${name}`, kind, parentId, false);
      }
    }
  };

  for (const statement of sourceFile.statements) {
    visitStatement(statement, "", null);
  }

  symbols.sort(
    (a, b) => a.range.startLine - b.range.startLine || a.range.startColumn - b.range.startColumn,
  );

  return { symbols, declarationIds, kindById };
}
