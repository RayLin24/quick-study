import { readFileSync } from "node:fs";
import path from "node:path";

import ts from "typescript";

import type { DiscoveredFile } from "./discovery.ts";

const COMPILER_OPTIONS: ts.CompilerOptions = {
  allowJs: true,
  checkJs: false,
  target: ts.ScriptTarget.ES2022,
  module: ts.ModuleKind.ESNext,
  moduleResolution: ts.ModuleResolutionKind.Bundler,
  allowImportingTsExtensions: true,
  resolveJsonModule: false,
  noEmit: true,
  noLib: false,
  skipLibCheck: true,
  skipDefaultLibCheck: true,
  // An untrusted repository must never influence the analyzer through @types packages.
  types: [],
};

/** Default library files are immutable, so parsing them once per process is safe. */
const libSourceFileCache = new Map<string, ts.SourceFile>();

const useCaseSensitiveFileNames = ts.sys.useCaseSensitiveFileNames;

function toKey(fileName: string): string {
  const normalized = path.resolve(fileName).split(path.sep).join("/");
  return useCaseSensitiveFileNames ? normalized : normalized.toLowerCase();
}

export interface RestrictedProgram {
  program: ts.Program;
  checker: ts.TypeChecker;
  /** Source files of the analysis set, in discovery order. */
  sourceFiles: ts.SourceFile[];
  /** Maps a source file to its root-relative path. */
  relativePathOf(sourceFile: ts.SourceFile): string | undefined;
  isInAnalysisSet(fileName: string): boolean;
  relativePathOfFileName(fileName: string): string | undefined;
}

/**
 * Builds a TypeScript program whose host can only ever see the discovered sources plus
 * TypeScript's own bundled `lib.*.d.ts` files. Module resolution is served entirely from
 * memory, so analysis cannot reach node_modules, parent directories or symlink targets,
 * and no repository code is ever executed.
 */
export function createRestrictedProgram(
  root: string,
  files: DiscoveredFile[],
): RestrictedProgram {
  const byKey = new Map<string, DiscoveredFile>();
  const directories = new Set<string>();
  for (const file of files) {
    byKey.set(toKey(file.absolutePath), file);
    let directory = path.dirname(file.absolutePath);
    for (;;) {
      const key = toKey(directory);
      if (directories.has(key)) {
        break;
      }
      directories.add(key);
      const parent = path.dirname(directory);
      if (parent === directory || toKey(parent) === toKey(path.dirname(root))) {
        break;
      }
      directory = parent;
    }
  }

  const defaultLibFileName = ts.getDefaultLibFilePath(COMPILER_OPTIONS);
  const libDirectoryKey = toKey(path.dirname(defaultLibFileName));

  const isLibFile = (fileName: string): boolean => {
    const base = path.basename(fileName);
    return (
      toKey(path.dirname(fileName)) === libDirectoryKey &&
      base.startsWith("lib.") &&
      base.endsWith(".d.ts")
    );
  };

  const readAllowed = (fileName: string): string | undefined => {
    const known = byKey.get(toKey(fileName));
    if (known) {
      return known.text;
    }
    if (isLibFile(fileName)) {
      try {
        return readFileSync(fileName, "utf8");
      } catch {
        return undefined;
      }
    }
    return undefined;
  };

  const fileExists = (fileName: string): boolean =>
    byKey.has(toKey(fileName)) || isLibFile(fileName);

  const moduleResolutionHost: ts.ModuleResolutionHost = {
    fileExists,
    readFile: readAllowed,
    directoryExists: (directoryName) => directories.has(toKey(directoryName)),
    getCurrentDirectory: () => root,
    getDirectories: () => [],
    realpath: (fileName) => fileName,
    useCaseSensitiveFileNames,
  };

  const resolutionCache = ts.createModuleResolutionCache(
    root,
    (fileName) => (useCaseSensitiveFileNames ? fileName : fileName.toLowerCase()),
    COMPILER_OPTIONS,
  );

  const getSourceFile = (fileName: string, languageVersion: ts.ScriptTarget) => {
    const known = byKey.get(toKey(fileName));
    if (known) {
      return ts.createSourceFile(fileName, known.text, languageVersion, true);
    }
    if (!isLibFile(fileName)) {
      return undefined;
    }
    const cached = libSourceFileCache.get(toKey(fileName));
    if (cached) {
      return cached;
    }
    const text = readAllowed(fileName);
    if (text === undefined) {
      return undefined;
    }
    const parsed = ts.createSourceFile(fileName, text, languageVersion, true);
    libSourceFileCache.set(toKey(fileName), parsed);
    return parsed;
  };

  const host: ts.CompilerHost = {
    getSourceFile,
    getDefaultLibFileName: () => defaultLibFileName,
    getDefaultLibLocation: () => path.dirname(defaultLibFileName),
    writeFile: () => {
      throw new Error("The analyzer never writes files");
    },
    getCurrentDirectory: () => root,
    getCanonicalFileName: (fileName) =>
      useCaseSensitiveFileNames ? fileName : fileName.toLowerCase(),
    useCaseSensitiveFileNames: () => useCaseSensitiveFileNames,
    getNewLine: () => "\n",
    fileExists,
    readFile: readAllowed,
    directoryExists: (directoryName) => directories.has(toKey(directoryName)),
    getDirectories: () => [],
    realpath: (fileName) => fileName,
    resolveModuleNameLiterals: (moduleLiterals, containingFile) =>
      moduleLiterals.map((literal) =>
        ts.resolveModuleName(
          literal.text,
          containingFile,
          COMPILER_OPTIONS,
          moduleResolutionHost,
          resolutionCache,
        ),
      ),
  };

  const rootNames = files.map((file) => file.absolutePath);
  const program = ts.createProgram({ rootNames, options: COMPILER_OPTIONS, host });

  const sourceFiles: ts.SourceFile[] = [];
  const relativeByFile = new Map<ts.SourceFile, string>();
  for (const file of files) {
    const sourceFile = program.getSourceFile(file.absolutePath);
    if (sourceFile) {
      sourceFiles.push(sourceFile);
      relativeByFile.set(sourceFile, file.relativePath);
    }
  }

  return {
    program,
    checker: program.getTypeChecker(),
    sourceFiles,
    relativePathOf: (sourceFile) => relativeByFile.get(sourceFile),
    isInAnalysisSet: (fileName) => byKey.has(toKey(fileName)),
    relativePathOfFileName: (fileName) => byKey.get(toKey(fileName))?.relativePath,
  };
}

/**
 * Resolves a module specifier against the in-memory analysis set only.
 * Returns the root-relative path when the target is part of the analysis, otherwise null.
 */
export function resolveToAnalysisSet(
  program: RestrictedProgram,
  sourceFile: ts.SourceFile,
  specifier: string,
): string | null {
  const resolved = ts.resolveModuleName(specifier, sourceFile.fileName, COMPILER_OPTIONS, {
    fileExists: (fileName) => program.isInAnalysisSet(fileName),
    readFile: () => undefined,
    useCaseSensitiveFileNames,
  });
  const fileName = resolved.resolvedModule?.resolvedFileName;
  if (!fileName) {
    return null;
  }
  return program.relativePathOfFileName(fileName) ?? null;
}
