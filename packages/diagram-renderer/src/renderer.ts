import { getEnvironment, resetDocument, type RenderEnvironment } from "./environment.ts";
import { inspectSource } from "./guard.ts";
import { resolveLimits } from "./limits.ts";
import { sanitizeSvg } from "./sanitize.ts";
import {
  EMPTY_SANITIZATION,
  SCHEMA_VERSION,
  TOOL_NAME,
  type DiagramError,
  type Limits,
  type RenderOptions,
  type RenderResult,
  type SanitizationReport,
  type ValidationResult,
} from "./types.ts";
import { mermaidVersion, packageVersion } from "./version.ts";

/** Mermaid keeps global configuration and a shared document, so renders must not interleave. */
let queue: Promise<unknown> = Promise.resolve();

function serialize<T>(task: () => Promise<T>): Promise<T> {
  const run = queue.then(task, task);
  queue = run.then(
    () => undefined,
    () => undefined,
  );
  return run;
}

function sanitizeId(raw: string): string {
  const cleaned = raw.replace(/[^A-Za-z0-9_-]/g, "-").replace(/^-+/, "");
  return /^[A-Za-z]/.test(cleaned) ? cleaned : `d-${cleaned}`;
}

function classifyParseError(error: unknown): DiagramError {
  const message = error instanceof Error ? error.message : String(error);
  const name = error instanceof Error ? error.name : "";

  if (name === "UnknownDiagramError" || /No diagram type detected/i.test(message)) {
    return {
      stage: "parse",
      code: "parse.unknown-diagram-type",
      message: message.split("\n")[0] ?? message,
      line: null,
      column: null,
    };
  }

  const location = /Parse error on line (\d+)/i.exec(message);
  return {
    stage: "parse",
    code: "parse.syntax-error",
    message,
    line: location?.[1] ? Number(location[1]) : null,
    column: null,
  };
}

async function parseSource(
  environment: RenderEnvironment,
  source: string,
): Promise<{ ok: true; diagramType: string } | { ok: false; error: DiagramError }> {
  try {
    const parsed = await environment.mermaid.parse(source, { suppressErrors: false });
    if (!parsed) {
      return {
        ok: false,
        error: {
          stage: "parse",
          code: "parse.syntax-error",
          message: "Mermaid rejected the diagram without a reason",
          line: null,
          column: null,
        },
      };
    }
    return { ok: true, diagramType: parsed.diagramType };
  } catch (error) {
    return { ok: false, error: classifyParseError(error) };
  }
}

function timeoutError(limits: Limits): DiagramError {
  return {
    stage: "render",
    code: "render.timeout",
    message: `Rendering exceeded the ${limits.renderTimeoutMs} ms budget`,
    line: null,
    column: null,
  };
}

function buildResult(
  ok: boolean,
  diagramType: string | null,
  svg: string | null,
  error: DiagramError | null,
  sanitization: SanitizationReport,
  limits: Limits,
  source: string,
  startedAt: number,
): RenderResult {
  return {
    schemaVersion: SCHEMA_VERSION,
    tool: { name: TOOL_NAME, version: packageVersion(), mermaid: mermaidVersion() },
    ok,
    diagramType,
    svg,
    error,
    sanitization,
    stats: {
      inputBytes: Buffer.byteLength(source, "utf8"),
      inputLines: source.length === 0 ? 0 : source.split(/\r?\n/).length,
      outputBytes: svg === null ? 0 : Buffer.byteLength(svg, "utf8"),
      durationMs: Math.round(performance.now() - startedAt),
    },
    limits,
  };
}

/** Runs `mermaid.parse()` only. Cheap enough to use as a pre-flight gate. */
export async function validateDiagram(
  source: string,
  options: RenderOptions = {},
): Promise<ValidationResult> {
  const limits = resolveLimits(options.limits);
  const inputError = inspectSource(source, limits);
  if (inputError) {
    return { ok: false, diagramType: null, error: inputError };
  }

  return serialize(async () => {
    const environment = await getEnvironment();
    const parsed = await parseSource(environment, source);
    return parsed.ok
      ? { ok: true, diagramType: parsed.diagramType, error: null }
      : { ok: false, diagramType: null, error: parsed.error };
  });
}

/**
 * Validates with `mermaid.parse()`, renders with a pinned Mermaid at `securityLevel: "strict"`,
 * then sanitizes the SVG. A failure at any stage yields `svg: null`; a broken or unsafe diagram
 * is never emitted.
 */
export async function renderDiagram(
  source: string,
  options: RenderOptions = {},
): Promise<RenderResult> {
  const startedAt = performance.now();
  const limits = resolveLimits(options.limits);
  const fail = (error: DiagramError, diagramType: string | null = null): RenderResult =>
    buildResult(false, diagramType, null, error, EMPTY_SANITIZATION, limits, source, startedAt);

  const inputError = inspectSource(source, limits);
  if (inputError) {
    return fail(inputError);
  }

  return serialize(async () => {
    const environment = await getEnvironment();

    const parsed = await parseSource(environment, source);
    if (!parsed.ok) {
      return fail(parsed.error);
    }

    const id = sanitizeId(options.id ?? "diagram");
    // The budget covers the render stage only. Creating the DOM environment and loading Mermaid
    // happen once per process and must not count against an individual diagram.
    const renderStartedAt = performance.now();
    let raw: string;
    try {
      // The race bounds how long a caller waits; the elapsed-time check below is the verdict,
      // because Mermaid's layout pass is synchronous and cannot be pre-empted.
      const rendered = await Promise.race([
        environment.mermaid.render(id, source),
        new Promise<null>((resolve) => {
          const timer = setTimeout(() => resolve(null), limits.renderTimeoutMs);
          timer.unref?.();
        }),
      ]);
      if (rendered === null) {
        return fail(timeoutError(limits), parsed.diagramType);
      }
      raw = rendered.svg;
    } catch (error) {
      return fail(
        {
          stage: "render",
          code: "render.failed",
          message: error instanceof Error ? error.message : String(error),
          line: null,
          column: null,
        },
        parsed.diagramType,
      );
    } finally {
      resetDocument(environment.window);
    }

    if (performance.now() - renderStartedAt >= limits.renderTimeoutMs) {
      return fail(timeoutError(limits), parsed.diagramType);
    }

    if (Buffer.byteLength(raw, "utf8") > limits.maxOutputBytes) {
      return fail(
        {
          stage: "render",
          code: "render.output-too-large",
          message: `Mermaid produced ${Buffer.byteLength(raw, "utf8")} bytes, above the ${limits.maxOutputBytes} byte limit`,
          line: null,
          column: null,
        },
        parsed.diagramType,
      );
    }

    const sanitized = sanitizeSvg(raw);
    if (!sanitized.ok) {
      return fail(sanitized.error, parsed.diagramType);
    }

    const outputBytes = Buffer.byteLength(sanitized.svg, "utf8");
    if (outputBytes > limits.maxOutputBytes) {
      return fail(
        {
          stage: "render",
          code: "render.output-too-large",
          message: `The sanitized SVG is ${outputBytes} bytes, above the ${limits.maxOutputBytes} byte limit`,
          line: null,
          column: null,
        },
        parsed.diagramType,
      );
    }

    return buildResult(
      true,
      parsed.diagramType,
      sanitized.svg,
      null,
      sanitized.report,
      limits,
      source,
      startedAt,
    );
  });
}
