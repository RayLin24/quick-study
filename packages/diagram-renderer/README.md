# @quick-study/diagram-renderer

Validates Mermaid sources with a pinned Mermaid release and renders them to sanitized SVG. It is
the diagram half of the tutorial quality gate and is designed to be spawned as a subprocess by
`apps/api`.

Every diagram goes through the same four stages, and a failure at any of them yields `svg: null`.
A broken or unsafe diagram is never emitted.

1. **Input guards** reject the source before Mermaid ever parses it: size and line limits,
   `click` interaction statements, dangerous HTML tags, `javascript:` URLs and `%%{init}%%`
   directives that would weaken the security posture.
2. **`mermaid.parse()`** validates the syntax and reports the diagram type.
3. **`mermaid.render()`** runs at `securityLevel: "strict"` with `htmlLabels` disabled.
4. **Sanitization** strips anything executable or externally referencing from the SVG.

## Install and build

```powershell
npm --prefix packages/diagram-renderer ci
npm --prefix packages/diagram-renderer run build
```

The compiled entry point is `packages/diagram-renderer/dist/cli.js`.

## CLI

```powershell
node packages/diagram-renderer/dist/cli.js --input <file|-> [options]
```

| Option | Meaning |
| --- | --- |
| `--input <file>` | Mermaid source file, or `-` for stdin. Default `-`. |
| `--out <file>` | Write the JSON result to a file instead of stdout. |
| `--svg-out <file>` | Write the sanitized SVG. Only written when the diagram passes. |
| `--id <name>` | Deterministic id for the SVG root and its scoped CSS. Default `diagram`. |
| `--validate-only` | Run `mermaid.parse()` only; the result carries `svg: null`. |
| `--pretty` | Indent the JSON output. |
| `--max-input-bytes <n>` | Reject sources larger than this. |
| `--max-input-lines <n>` | Reject sources with more lines than this. |
| `--max-output-bytes <n>` | Reject SVG output larger than this. |
| `--render-timeout-ms <n>` | Budget for the render stage. |
| `--print-schema` | Print the JSON Schema of the result and exit. |
| `--version`, `--help` | Print the version or the usage text. |

### Exit codes and streams

| Code | Meaning |
| --- | --- |
| `0` | The diagram passed. stdout holds the result document with `ok: true`. |
| `1` | Usage or I/O error. stderr holds `{"error":{"code":"usage",...}}` and stdout is empty. |
| `2` | The diagram was **rejected**. stdout still holds a valid result document with `ok: false` and a diagnosable `error`. |
| `3` | Internal error. stderr holds `{"error":{"code":"internal",...}}`. |

Exit code `2` is a verdict, not a crash: the caller parses stdout in exactly the same way as on
success and reads `error.stage`, `error.code` and `error.line` to explain the failure or to drive
a repair attempt.

### Calling from Python

```python
import json
import subprocess

completed = subprocess.run(
    ["node", "packages/diagram-renderer/dist/cli.js", "--input", "-", "--id", diagram_id],
    input=mermaid_source, capture_output=True, text=True, timeout=60, check=False,
)
if completed.returncode in (0, 2):
    result = json.loads(completed.stdout)
    if result["ok"]:
        svg = result["svg"]
    else:
        reason = result["error"]        # stage, code, message, line
else:
    failure = json.loads(completed.stderr)
```

Always pass an outer `timeout`. `--render-timeout-ms` bounds how long a caller waits for a
result, but Mermaid's layout pass is synchronous and cannot be pre-empted, so the process timeout
is the real backstop.

## Result contract

`--print-schema` emits the authoritative JSON Schema (draft 2020-12). `schemaVersion` follows
semantic versioning and a breaking change requires a new major version.

```jsonc
{
  "schemaVersion": "1.0.0",
  "tool": { "name": "@quick-study/diagram-renderer", "version": "0.1.0", "mermaid": "11.16.1" },
  "ok": true,
  "diagramType": "flowchart-v2",
  "svg": "<svg id=\"diagram\" ...>...</svg>",
  "error": null,
  "sanitization": { "removedElements": [], "removedAttributes": [], "modifiedStyleRules": 0 },
  "stats": { "inputBytes": 224, "inputLines": 8, "outputBytes": 18753, "durationMs": 879 },
  "limits": { "maxInputBytes": 65536, "maxInputLines": 2000, "maxOutputBytes": 4194304, "renderTimeoutMs": 15000 }
}
```

`stats.durationMs` is the only non-deterministic field: the same source and the same `--id`
produce a byte-identical SVG, so diagrams can be content-addressed.

### Error codes

`error.stage` is one of `input`, `parse`, `render` or `sanitize`, and `error.line` points into the
Mermaid source whenever the failure can be located.

| Stage | Code | Meaning |
| --- | --- | --- |
| `input` | `input.empty` | The source is blank. |
| `input` | `input.too-large` / `input.too-many-lines` | Above the configured size limits. |
| `input` | `input.interaction-directive` | A `click` or `callback` statement was found. |
| `input` | `input.raw-html` | A dangerous HTML tag was found in a label. |
| `input` | `input.unsafe-url` | A `javascript:`, `vbscript:` or `data:` URL was found. |
| `input` | `input.unsafe-init-directive` | An `%%{init}%%` directive tried to set a security-relevant key. |
| `parse` | `parse.syntax-error` | `mermaid.parse()` rejected the source; `line` is set. |
| `parse` | `parse.unknown-diagram-type` | No Mermaid diagram type matched the source. |
| `render` | `render.failed` | Mermaid threw while rendering. |
| `render` | `render.timeout` | The render stage exceeded its budget. |
| `render` | `render.output-too-large` | The SVG is above `maxOutputBytes`. |
| `sanitize` | `sanitize.no-svg-root` / `sanitize.empty-output` | The output could not be made safe. |

## Security

Input rejection happens before Mermaid runs, Mermaid itself runs at `securityLevel: "strict"`, and
the SVG is sanitized afterwards. Each layer is independent, so a gap in one does not become an
exploit.

`securityLevel: "strict"` already encodes HTML in labels and disables JavaScript callbacks, but a
`click A "https://..."` statement still emits an `<a href>` into the SVG, which is why interaction
statements are rejected outright.

The sanitizer removes, and reports in `sanitization`:

- `<script>`, SMIL animation (`animate`, `set`, …), `<foreignObject>` and any embedded HTML,
  `<iframe>`, `<object>`, `<embed>`, `<link>`, `<meta>`, `<base>` and form elements;
- every element outside the SVG namespace;
- every `on*` event handler attribute;
- every URL-bearing attribute whose target is not a same-document `#fragment`, which covers
  `http(s):`, `data:` and `javascript:` references in `href`, `xlink:href`, `src` and friends;
- `@import`, remote `url(...)`, `expression(...)`, `-moz-binding` and `behavior:` in both `<style>`
  blocks and `style` attributes;
- comments and processing instructions.

Rendering never fetches anything: there is no network access, no font loading and no external
stylesheet.

### Resource limits

Limits are overridable downward but clamped by hard ceilings; asking for more is an error.

| Limit | Default | Ceiling |
| --- | --- | --- |
| `maxInputBytes` | 64 KiB | 1 MiB |
| `maxInputLines` | 2000 | 20000 |
| `maxOutputBytes` | 4 MiB | 32 MiB |
| `renderTimeoutMs` | 15000 | 120000 |

## Programmatic use

```ts
import { renderDiagram, validateDiagram } from "@quick-study/diagram-renderer";

const check = await validateDiagram(source);          // parse only
const result = await renderDiagram(source, { id: "architecture" });
```

Renders are serialized internally because Mermaid holds global configuration and a shared
document. **Importing this package installs a jsdom DOM on `globalThis`**, since Mermaid reads
`document` from module scope. Prefer the CLI, which isolates that in its own process.

## Rendering without a browser

Mermaid normally needs a browser to measure text. This package runs it on jsdom and supplies the
missing SVG geometry itself: `getBBox`, `getComputedTextLength` and friends are computed from the
document by `src/geometry.ts`, which understands shape attributes, transform lists, path commands
and estimated text extents.

Two consequences are worth knowing:

- Text widths are estimated from character counts, not from real font metrics, so a rendered
  diagram is very close to a browser's layout but not identical to the pixel.
- `getBBox()` deliberately returns a bare `SVGRect`. Mermaid places several shapes from
  `bbox.left ?? 0` and `bbox.top ?? 0`, so adding DOMRect keys would silently push labels out of
  their shapes.

## Development

```powershell
npm --prefix packages/diagram-renderer run typecheck
npm --prefix packages/diagram-renderer run test:fast   # tests against the TypeScript sources
npm --prefix packages/diagram-renderer test            # build, then the full suite including dist/cli.js
```

Tests run on Node's built-in runner against the TypeScript sources directly, using Node's native
type stripping. Node 22.18 or newer is required.
