export interface Matrix {
  a: number;
  b: number;
  c: number;
  d: number;
  e: number;
  f: number;
}

export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

export type TextAnchor = "start" | "middle" | "end";

interface Point {
  x: number;
  y: number;
}

/**
 * jsdom has no layout engine, so text extents are estimated. The ratios approximate the
 * default Mermaid sans-serif stack closely enough for dagre to lay a diagram out and for the
 * resulting viewBox to contain it.
 */
const CHAR_WIDTH_RATIO = 0.55;
const LINE_HEIGHT_RATIO = 1.25;
const BASELINE_RATIO = 0.8;
const DEFAULT_FONT_SIZE = 16;

const IDENTITY: Matrix = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

/** deepStrictEqual distinguishes -0 from 0, so normalise every component. */
function norm(value: number): number {
  return value === 0 ? 0 : value;
}

function matrix(a: number, b: number, c: number, d: number, e: number, f: number): Matrix {
  return { a: norm(a), b: norm(b), c: norm(c), d: norm(d), e: norm(e), f: norm(f) };
}

export function multiplyMatrices(left: Matrix, right: Matrix): Matrix {
  return matrix(
    left.a * right.a + left.c * right.b,
    left.b * right.a + left.d * right.b,
    left.a * right.c + left.c * right.d,
    left.b * right.c + left.d * right.d,
    left.a * right.e + left.c * right.f + left.e,
    left.b * right.e + left.d * right.f + left.f,
  );
}

function numbersIn(value: string): number[] {
  return (value.match(/-?\d*\.?\d+(?:e[-+]?\d+)?/gi) ?? [])
    .map(Number)
    .filter((entry) => Number.isFinite(entry));
}

export function parseTransformList(value: string | null | undefined): Matrix {
  if (!value) {
    return { ...IDENTITY };
  }

  let result: Matrix = { ...IDENTITY };
  const pattern = /(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)/g;

  for (let match = pattern.exec(value); match !== null; match = pattern.exec(value)) {
    const args = numbersIn(match[2] ?? "");
    const [first = 0, second, third] = args;
    let step: Matrix;

    switch (match[1]) {
      case "matrix":
        step = matrix(args[0] ?? 1, args[1] ?? 0, args[2] ?? 0, args[3] ?? 1, args[4] ?? 0, args[5] ?? 0);
        break;
      case "translate":
        step = matrix(1, 0, 0, 1, first, second ?? 0);
        break;
      case "scale":
        step = matrix(first, 0, 0, second ?? first, 0, 0);
        break;
      case "rotate": {
        const radians = (first * Math.PI) / 180;
        const cos = Math.cos(radians);
        const sin = Math.sin(radians);
        const rotation = matrix(cos, sin, -sin, cos, 0, 0);
        if (second === undefined || third === undefined) {
          step = rotation;
        } else {
          step = multiplyMatrices(
            multiplyMatrices(matrix(1, 0, 0, 1, second, third), rotation),
            matrix(1, 0, 0, 1, -second, -third),
          );
        }
        break;
      }
      case "skewX":
        step = matrix(1, 0, Math.tan((first * Math.PI) / 180), 1, 0, 0);
        break;
      case "skewY":
        step = matrix(1, Math.tan((first * Math.PI) / 180), 0, 1, 0, 0);
        break;
      default:
        step = { ...IDENTITY };
    }

    result = multiplyMatrices(result, step);
  }

  return result;
}

export function transformBox(transform: Matrix, box: Box): Box {
  const corners: Point[] = [
    { x: box.x, y: box.y },
    { x: box.x + box.width, y: box.y },
    { x: box.x, y: box.y + box.height },
    { x: box.x + box.width, y: box.y + box.height },
  ].map((point) => ({
    x: transform.a * point.x + transform.c * point.y + transform.e,
    y: transform.b * point.x + transform.d * point.y + transform.f,
  }));

  const xs = corners.map((point) => point.x);
  const ys = corners.map((point) => point.y);
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  return { x: norm(minX), y: norm(minY), width: Math.max(...xs) - minX, height: Math.max(...ys) - minY };
}

function unionBoxes(left: Box | null, right: Box | null): Box | null {
  if (!left) {
    return right;
  }
  if (!right) {
    return left;
  }
  const x = Math.min(left.x, right.x);
  const y = Math.min(left.y, right.y);
  const right_ = Math.max(left.x + left.width, right.x + right.width);
  const bottom = Math.max(left.y + left.height, right.y + right.height);
  return { x: norm(x), y: norm(y), width: right_ - x, height: bottom - y };
}

function numberAttribute(element: Element, name: string, fallback = 0): number {
  const raw = element.getAttribute(name);
  if (raw === null) {
    return fallback;
  }
  const value = Number.parseFloat(raw);
  return Number.isFinite(value) ? value : fallback;
}

function fontSizeOf(element: Element): number {
  let current: Element | null = element;
  while (current) {
    const attribute = current.getAttribute("font-size") ?? styleValue(current, "font-size");
    if (attribute) {
      const value = Number.parseFloat(attribute);
      if (Number.isFinite(value) && value > 0) {
        return value;
      }
    }
    current = current.parentElement;
  }
  return DEFAULT_FONT_SIZE;
}

function styleValue(element: Element, property: string): string | null {
  const style = element.getAttribute("style");
  if (!style) {
    return null;
  }
  const match = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, "i").exec(style);
  return match?.[1]?.trim() ?? null;
}

interface AnchorRule {
  selector: string;
  value: string;
}

const anchorRuleCache = new WeakMap<Document, AnchorRule[]>();

/**
 * Mermaid centres node labels with a stylesheet rule, not with a `text-anchor` attribute, and
 * jsdom's `getComputedStyle` does not resolve SVG presentation properties. Selectors are read
 * straight out of the `<style>` elements and matched with `Element.matches`.
 */
function anchorRulesOf(document: Document): AnchorRule[] {
  const cached = anchorRuleCache.get(document);
  if (cached) {
    return cached;
  }

  const rules: AnchorRule[] = [];
  for (const style of document.getElementsByTagName("style")) {
    const css = style.textContent ?? "";
    for (const block of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
      const declaration = /(?:^|;)\s*text-anchor\s*:\s*([a-zA-Z-]+)/.exec(block[2] ?? "");
      if (!declaration?.[1]) {
        continue;
      }
      for (const selector of (block[1] ?? "").split(",")) {
        const trimmed = selector.trim();
        if (trimmed.length > 0 && !trimmed.startsWith("@")) {
          rules.push({ selector: trimmed, value: declaration[1] });
        }
      }
    }
  }

  anchorRuleCache.set(document, rules);
  return rules;
}

function anchorFromStylesheet(element: Element): string | null {
  const document = element.ownerDocument;
  if (!document) {
    return null;
  }
  let matched: string | null = null;
  for (const rule of anchorRulesOf(document)) {
    try {
      if (element.matches(rule.selector)) {
        matched = rule.value;
      }
    } catch {
      // Ignore selectors the DOM implementation cannot parse.
    }
  }
  return matched;
}

function textAnchorOf(element: Element, fallback: TextAnchor): string {
  let current: Element | null = element;
  while (current) {
    // Inline style beats a stylesheet rule, which in turn beats a presentation attribute.
    const anchor =
      styleValue(current, "text-anchor") ??
      anchorFromStylesheet(current) ??
      current.getAttribute("text-anchor");
    if (anchor) {
      return anchor.trim();
    }
    current = current.parentElement;
  }
  return fallback;
}

/** Resolves an SVG length that may carry `em` or `ex` units. */
function resolveLength(raw: string | null, fontSize: number, fallback: number | null): number | null {
  if (raw === null) {
    return fallback;
  }
  const trimmed = raw.trim();
  const value = Number.parseFloat(trimmed);
  if (!Number.isFinite(value)) {
    return fallback;
  }
  if (/em$/i.test(trimmed)) {
    return value * fontSize;
  }
  if (/ex$/i.test(trimmed)) {
    return value * fontSize * 0.5;
  }
  return value;
}

interface TextRow {
  text: string;
  baseline: number;
  x: number | null;
}

/**
 * Splits a text element into rows. Mermaid emits one `<tspan>` per visual row and puts the
 * baseline on that row through `y` and `dy`, so those offsets decide the vertical extent.
 */
function textRows(element: Element, fontSize: number): TextRow[] {
  const textY = resolveLength(element.getAttribute("y"), fontSize, 0) ?? 0;
  const children = [...element.children].filter(
    (child) => child.localName.toLowerCase() === "tspan",
  );

  if (children.length === 0) {
    return [
      {
        text: element.textContent ?? "",
        baseline: textY,
        x: resolveLength(element.getAttribute("x"), fontSize, null),
      },
    ];
  }

  const rows: TextRow[] = [];
  let cursor = textY;
  for (const child of children) {
    const absolute = resolveLength(child.getAttribute("y"), fontSize, null);
    const delta = resolveLength(child.getAttribute("dy"), fontSize, 0) ?? 0;
    const baseline = (absolute ?? cursor) + delta;
    cursor = baseline;
    rows.push({
      text: child.textContent ?? "",
      baseline,
      x:
        resolveLength(child.getAttribute("x"), fontSize, null) ??
        resolveLength(element.getAttribute("x"), fontSize, null),
    });
  }
  return rows;
}

function textBox(element: Element, defaultAnchor: TextAnchor): Box | null {
  const fontSize = fontSizeOf(element);
  const rows = textRows(element, fontSize);
  const width = rows.reduce(
    (widest, row) => Math.max(widest, [...row.text].length * fontSize * CHAR_WIDTH_RATIO),
    0,
  );
  const baselines = rows.map((row) => row.baseline);
  const top = Math.min(...baselines) - fontSize * BASELINE_RATIO;
  const bottom = Math.max(...baselines) + fontSize * (LINE_HEIGHT_RATIO - BASELINE_RATIO);
  const height = bottom - top;
  if (width === 0 && height === 0) {
    return null;
  }

  const anchor = textAnchorOf(element, defaultAnchor);
  const originX = rows[0]?.x ?? 0;
  const x = anchor === "middle" ? originX - width / 2 : anchor === "end" ? originX - width : originX;
  return { x: norm(x), y: norm(top), width, height };
}

function pointsBox(value: string | null): Box | null {
  if (!value) {
    return null;
  }
  const numbers = numbersIn(value);
  if (numbers.length < 2) {
    return null;
  }
  const xs: number[] = [];
  const ys: number[] = [];
  for (let index = 0; index + 1 < numbers.length; index += 2) {
    xs.push(numbers[index] as number);
    ys.push(numbers[index + 1] as number);
  }
  const minX = Math.min(...xs);
  const minY = Math.min(...ys);
  return { x: norm(minX), y: norm(minY), width: Math.max(...xs) - minX, height: Math.max(...ys) - minY };
}

interface BoxAccumulator {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
  seen: boolean;
}

function include(accumulator: BoxAccumulator, x: number, y: number): void {
  accumulator.minX = Math.min(accumulator.minX, x);
  accumulator.minY = Math.min(accumulator.minY, y);
  accumulator.maxX = Math.max(accumulator.maxX, x);
  accumulator.maxY = Math.max(accumulator.maxY, y);
  accumulator.seen = true;
}

/**
 * Bounds a path by walking its commands. Curve control points are included, which over-estimates
 * a curve but never clips it. An arc is bounded by its chord widened by the radius perpendicular
 * to that chord, which is exact for the half-ellipse caps Mermaid draws on cylinders.
 */
function pathBox(data: string | null): Box | null {
  if (!data) {
    return null;
  }

  const accumulator: BoxAccumulator = {
    minX: Infinity,
    minY: Infinity,
    maxX: -Infinity,
    maxY: -Infinity,
    seen: false,
  };
  let x = 0;
  let y = 0;
  let startX = 0;
  let startY = 0;

  for (const segment of data.matchAll(/([MmLlHhVvCcSsQqTtAaZz])([^MmLlHhVvCcSsQqTtAaZz]*)/g)) {
    const command = segment[1] as string;
    const values = numbersIn(segment[2] ?? "");
    const relative = command === command.toLowerCase();
    const at = (index: number): number => values[index] ?? 0;

    if (command === "Z" || command === "z") {
      x = startX;
      y = startY;
      include(accumulator, x, y);
      continue;
    }

    const stride =
      command === "H" || command === "h" || command === "V" || command === "v"
        ? 1
        : command === "C" || command === "c"
          ? 6
          : command === "S" || command === "s" || command === "Q" || command === "q"
            ? 4
            : command === "A" || command === "a"
              ? 7
              : 2;

    for (let offset = 0; offset + stride <= values.length; offset += stride) {
      const base = offset;
      switch (command.toUpperCase()) {
        case "M":
        case "L":
        case "T": {
          x = relative ? x + at(base) : at(base);
          y = relative ? y + at(base + 1) : at(base + 1);
          if (command === "M" || command === "m") {
            startX = x;
            startY = y;
          }
          include(accumulator, x, y);
          break;
        }
        case "H": {
          x = relative ? x + at(base) : at(base);
          include(accumulator, x, y);
          break;
        }
        case "V": {
          y = relative ? y + at(base) : at(base);
          include(accumulator, x, y);
          break;
        }
        case "C": {
          const points = [
            [at(base), at(base + 1)],
            [at(base + 2), at(base + 3)],
            [at(base + 4), at(base + 5)],
          ] as const;
          for (const [pointX, pointY] of points) {
            include(accumulator, relative ? x + pointX : pointX, relative ? y + pointY : pointY);
          }
          x = relative ? x + at(base + 4) : at(base + 4);
          y = relative ? y + at(base + 5) : at(base + 5);
          break;
        }
        case "S":
        case "Q": {
          const points = [
            [at(base), at(base + 1)],
            [at(base + 2), at(base + 3)],
          ] as const;
          for (const [pointX, pointY] of points) {
            include(accumulator, relative ? x + pointX : pointX, relative ? y + pointY : pointY);
          }
          x = relative ? x + at(base + 2) : at(base + 2);
          y = relative ? y + at(base + 3) : at(base + 3);
          break;
        }
        case "A": {
          const rx = Math.abs(at(base));
          const ry = Math.abs(at(base + 1));
          const endX = relative ? x + at(base + 5) : at(base + 5);
          const endY = relative ? y + at(base + 6) : at(base + 6);
          const bulgeVertically = Math.abs(endX - x) >= Math.abs(endY - y);
          include(accumulator, x, y);
          include(accumulator, endX, endY);
          if (bulgeVertically) {
            include(accumulator, Math.min(x, endX), Math.min(y, endY) - ry);
            include(accumulator, Math.max(x, endX), Math.max(y, endY) + ry);
          } else {
            include(accumulator, Math.min(x, endX) - rx, Math.min(y, endY));
            include(accumulator, Math.max(x, endX) + rx, Math.max(y, endY));
          }
          x = endX;
          y = endY;
          break;
        }
        default:
          break;
      }
    }
  }

  if (!accumulator.seen) {
    return null;
  }
  return {
    x: norm(accumulator.minX),
    y: norm(accumulator.minY),
    width: accumulator.maxX - accumulator.minX,
    height: accumulator.maxY - accumulator.minY,
  };
}

/**
 * Bounding box of an element's own geometry, ignoring its children and its own transform.
 */
function shapeBox(element: Element, defaultAnchor: TextAnchor): Box | null {
  switch (element.localName.toLowerCase()) {
    case "rect":
    case "image":
    case "use":
    case "svg": {
      const width = numberAttribute(element, "width");
      const height = numberAttribute(element, "height");
      if (width === 0 && height === 0) {
        return null;
      }
      return { x: numberAttribute(element, "x"), y: numberAttribute(element, "y"), width, height };
    }
    case "circle": {
      const r = numberAttribute(element, "r");
      if (r === 0) {
        return null;
      }
      return {
        x: numberAttribute(element, "cx") - r,
        y: numberAttribute(element, "cy") - r,
        width: r * 2,
        height: r * 2,
      };
    }
    case "ellipse": {
      const rx = numberAttribute(element, "rx");
      const ry = numberAttribute(element, "ry");
      if (rx === 0 && ry === 0) {
        return null;
      }
      return {
        x: numberAttribute(element, "cx") - rx,
        y: numberAttribute(element, "cy") - ry,
        width: rx * 2,
        height: ry * 2,
      };
    }
    case "line": {
      const x1 = numberAttribute(element, "x1");
      const y1 = numberAttribute(element, "y1");
      const x2 = numberAttribute(element, "x2");
      const y2 = numberAttribute(element, "y2");
      return {
        x: Math.min(x1, x2),
        y: Math.min(y1, y2),
        width: Math.abs(x2 - x1),
        height: Math.abs(y2 - y1),
      };
    }
    case "polygon":
    case "polyline":
      return pointsBox(element.getAttribute("points"));
    case "path":
      return pathBox(element.getAttribute("d"));
    case "text":
    case "tspan":
      return textBox(element, defaultAnchor);
    default:
      return null;
  }
}

export interface BoundingBoxOptions {
  /**
   * Anchor assumed when neither an attribute, an inline style nor a stylesheet rule applies.
   * The SVG default is `start`, but Mermaid centres label text through a stylesheet that is not
   * attached yet while it measures, so the renderer passes `middle`.
   */
  defaultTextAnchor?: TextAnchor;
}

/**
 * Mirrors `SVGGraphicsElement.getBBox()`: the union of the element's own geometry and of every
 * descendant, expressed in the element's own user space, so the element's own transform is not
 * applied but its children's transforms are.
 */
export function computeBoundingBox(element: Element, options: BoundingBoxOptions = {}): Box | null {
  const defaultAnchor = options.defaultTextAnchor ?? "start";
  const tag = element.localName.toLowerCase();
  if (tag === "text" || tag === "tspan") {
    return shapeBox(element, defaultAnchor);
  }

  let box = shapeBox(element, defaultAnchor);
  for (const child of element.children) {
    const childBox = computeBoundingBox(child, options);
    if (!childBox) {
      continue;
    }
    box = unionBoxes(box, transformBox(parseTransformList(child.getAttribute("transform")), childBox));
  }
  return box;
}

export function estimateTextWidth(text: string, fontSize: number): number {
  return [...text].length * fontSize * CHAR_WIDTH_RATIO;
}
