# @quick-study/ts-analyzer

Static JavaScript/TypeScript analysis built on the TypeScript Compiler API. It emits one JSON
document describing files, symbols, imports, file-level dependencies and call edges, and it is
designed to be spawned as a subprocess by the Python repository analyzer in `apps/api`.

The analyzer **never executes the repository it reads**. It only parses and type-checks, and the
compiler host is restricted so that resolution can only ever reach the files that were explicitly
collected plus TypeScript's own bundled `lib.*.d.ts` files.

## Install and build

```powershell
npm --prefix packages/ts-analyzer ci
npm --prefix packages/ts-analyzer run build
```

The compiled entry point is `packages/ts-analyzer/dist/cli.js`.

## CLI

```powershell
node packages/ts-analyzer/dist/cli.js --root <repo-dir> [options] [path...]
```

Paths are resolved against `--root` and must stay inside it; an entry that escapes the root is a
usage error. With no path at all the whole root is analyzed.

| Option | Meaning |
| --- | --- |
| `--root <dir>` | Analysis root, default the current directory. Every emitted path is relative to it. |
| `--dir <dir>` | Add a directory to analyze. Repeatable. |
| `--file <file>` | Add a single file to analyze. Repeatable. |
| `--files-from <file>` | Read newline-delimited paths from a file, or `-` for stdin. `#` starts a comment. |
| `--out <file>` | Write the JSON document to a file. stdout stays empty. |
| `--pretty` | Indent the JSON output. |
| `--max-files <n>` | Maximum number of source files. |
| `--max-file-bytes <n>` | Skip files larger than this. |
| `--max-total-bytes <n>` | Maximum total bytes of source read into memory. |
| `--max-directory-depth <n>` | Maximum directory recursion depth. |
| `--time-budget-ms <n>` | Wall-clock budget for the whole analysis. |
| `--strict-limits` | Exit with code 2 instead of returning a truncated result. |
| `--print-schema` | Print the JSON Schema of the output and exit. |
| `--version`, `--help` | Print the version or the usage text. |

### Exit codes and streams

| Code | Meaning |
| --- | --- |
| `0` | Analysis completed. stdout holds exactly one JSON document. It may still be truncated, so check `limits.truncated`. |
| `1` | Usage or I/O error. stderr holds `{"error":{"code":"usage","message":...}}`. |
| `2` | A limit was exceeded while `--strict-limits` was set. stderr holds `{"error":{"code":"limit-exceeded","truncationReasons":[...]}}`. |
| `3` | Internal error. stderr holds `{"error":{"code":"internal","message":...}}`. |

On exit code `0` stdout contains nothing but the JSON document, so a caller can parse it directly.
Every failure mode writes machine-readable JSON to stderr and leaves stdout empty.

### Calling from Python

```python
import json
import subprocess

completed = subprocess.run(
    ["node", "packages/ts-analyzer/dist/cli.js", "--root", repo_dir,
     "--max-files", "2000", "--time-budget-ms", "60000"],
    capture_output=True, text=True, timeout=120, check=False,
)
if completed.returncode == 0:
    analysis = json.loads(completed.stdout)
else:
    failure = json.loads(completed.stderr)
```

Always pass an outer `timeout`: `--time-budget-ms` bounds the analysis phases, but the outer
timeout is the only defence against a pathological input that stalls the parser itself.

## Output contract

`--print-schema` emits the authoritative JSON Schema (draft 2020-12). `schemaVersion` follows
semantic versioning and a breaking change to the document requires a new major version.

Top-level shape:

```jsonc
{
  "schemaVersion": "1.0.0",
  "tool": { "name": "@quick-study/ts-analyzer", "version": "0.1.0", "typescript": "5.9.3" },
  "root": "D:/repos/example",
  "files": [ /* path, language, bytes, lines, sha256, syntaxErrors */ ],
  "symbols": [ /* id, name, qualifiedName, kind, file, range, exported, ... */ ],
  "imports": [ /* file, moduleSpecifier, kind, typeOnly, resolution, bindings, ... */ ],
  "dependencies": [ /* from, to, scope, count */ ],
  "callEdges": [ /* from, to, resolution, confidence, reason, callKind, ... */ ],
  "diagnostics": [ /* severity, code, message, path */ ],
  "limits": { "applied": { }, "truncated": false, "truncationReasons": [] },
  "stats": { },
  "timing": { "durationMs": 459 }
}
```

Output is deterministic for identical input: every array is sorted by a stable key. The only
exceptions are `timing.durationMs` and `root`, which depend on the machine. Drop both before
content-addressing the document.

Symbol ids are `<file>#<qualifiedName>`, for example `src/service.ts#Repository.load`. A name that
is declared more than once in a file gets a `~2`, `~3` suffix.

### Honesty rules for call edges

Nothing that cannot be established statically is reported as a fact. Every edge carries a
`resolution`, a `confidence` and a machine-readable `reason`, and `to` is populated **only** when
the target is a symbol that also appears in `symbols`.

| `resolution` | `to` | Meaning |
| --- | --- | --- |
| `resolved` | symbol id, or `null` with `resolvedFile` for module loads | The type checker found a unique declaration inside the analysis set. |
| `external` | `null` | The target is real but outside the analysis set (a package, a Node builtin, or `lib.*.d.ts`). `externalModule` names the package when it is known. |
| `ambiguous` | `null` | Several candidate declarations, for example merged declarations or overloads across files. |
| `unresolved` | `null` | Not decidable statically. Always `confidence: "low"`. |

| `confidence` | When |
| --- | --- |
| `high` | A unique top-level declaration: plain function, class or constructor call. |
| `medium` | A class member that a subclass could override, or a call into a module outside the analysis set. |
| `low` | Anything unresolved or ambiguous. |

`reason` uses a closed vocabulary that the schema pins:

| Reason | Emitted for |
| --- | --- |
| `checker-unique-declaration` | The checker resolved exactly one declaration. |
| `checker-multiple-declarations` | Several distinct declarations matched. |
| `declaration-outside-analysis-set` | Resolved into `lib.*.d.ts` or a file that was not collected. |
| `external-module` | The callee reaches the code through an import of an external module. |
| `import-literal` / `require-literal` | `import("./x")` / `require("./x")` with a literal specifier. |
| `callee-type-any` | The receiver's type is `any`, so dispatch is unknown. |
| `computed-member-access` | `obj[expr]()` with a non-literal key. |
| `no-declaration-for-callee` | The checker produced no declaration, for example an index signature. |
| `parameter-invocation` | A callback parameter is invoked. |
| `local-binding` | The callee is a local binding that is not a tracked symbol. |
| `dynamic-eval` | `eval(...)` or `new Function(...)`. |
| `dynamic-import-non-literal` / `require-non-literal` | The module specifier is computed at runtime. |

Types that come from packages outside the analysis set are unknown to the checker, so calls on
values of those types widen to `callee-type-any`. That is deliberate: an unknown receiver is
reported as unresolved rather than guessed.

## Safety and resource limits

- **No execution.** Only `ts.createProgram` parsing and type checking run. No repository script,
  build step, `tsconfig.json`, package manager or compiler plugin is ever loaded.
- **Closed file set.** The compiler host answers `fileExists` and `readFile` from an in-memory map
  of the collected sources plus TypeScript's own `lib.*.d.ts`. Module resolution therefore cannot
  reach `node_modules`, parent directories or anything else on disk.
- **No symlink traversal.** Symbolic links are recorded as a diagnostic and skipped.
- **No writes.** The compiler host's `writeFile` throws; the CLI only writes the file given to `--out`.
- **Skipped by default.** `node_modules`, `.git`, `dist`, `build`, `out`, `coverage`, `vendor`,
  `.next`, `target` and similar directories, plus `.d.ts` and `*.min.js` files.

Limits are overridable downward but are clamped by hard ceilings; asking for more is a usage error.

| Limit | Default | Ceiling |
| --- | --- | --- |
| `maxFiles` | 2000 | 20000 |
| `maxFileBytes` | 512 KiB | 4 MiB |
| `maxTotalBytes` | 48 MiB | 256 MiB |
| `maxDirectoryDepth` | 24 | 64 |
| `timeBudgetMs` | 60000 | 600000 |

When a limit truncates the run the result still parses: `limits.truncated` becomes `true`,
`limits.truncationReasons` lists codes such as `limit.max-files`, and `diagnostics` explains what
was dropped. Use `--strict-limits` when a partial analysis is not acceptable.

## Programmatic use

```ts
import { analyzeProject, getOutputSchema, DEFAULT_LIMITS } from "@quick-study/ts-analyzer";

const result = await analyzeProject({
  root: "/path/to/repo",
  entries: ["src"],
  limits: { maxFiles: 500 },
});
```

## Development

```powershell
npm --prefix packages/ts-analyzer run typecheck   # strict tsc, sources and tests
npm --prefix packages/ts-analyzer run test:fast   # tests against the TypeScript sources
npm --prefix packages/ts-analyzer test            # build, then the full suite including dist/cli.js
```

Tests run on Node's built-in runner against the TypeScript sources directly, using Node's native
type stripping, so no bundler or test framework is needed. Node 22.18 or newer is required.
