import { JSDOM } from "jsdom";
import type { Mermaid } from "mermaid";

import { computeBoundingBox, estimateTextWidth } from "./geometry.ts";
import { LIMIT_CEILINGS } from "./limits.ts";

export interface RenderEnvironment {
  window: JSDOM["window"];
  mermaid: Mermaid;
}

type LooseObject = Record<string, unknown>;

const DEFAULT_FONT_SIZE = 16;

function fontSizeOf(element: Element): number {
  let current: Element | null = element;
  while (current) {
    const raw = current.getAttribute("font-size");
    if (raw) {
      const value = Number.parseFloat(raw);
      if (Number.isFinite(value) && value > 0) {
        return value;
      }
    }
    current = current.parentElement;
  }
  return DEFAULT_FONT_SIZE;
}

/**
 * jsdom implements the SVG DOM but performs no layout, so Mermaid's calls to `getBBox()` and
 * friends return nothing and every node collapses. These shims compute geometry from the
 * document itself, which is deterministic and needs no browser.
 */
function installLayoutShims(window: JSDOM["window"]): void {
  const svgPrototype = window.SVGElement.prototype as unknown as LooseObject;

  // Mermaid measures labels before attaching the stylesheet that centres them, so the shim has
  // to assume the anchor that will eventually apply. Getting this wrong makes shapes that place
  // their label from the bounding box origin, such as cylinders, shift it by half its width.
  const boxOf = (element: Element) =>
    computeBoundingBox(element, { defaultTextAnchor: "middle" }) ?? {
      x: 0,
      y: 0,
      width: 0,
      height: 0,
    };

  // A real getBBox() returns an SVGRect, which has x/y/width/height and nothing else. Mermaid
  // branches on `bbox.left ?? 0` when placing labels, so adding DOMRect keys here would silently
  // shift labels out of shapes such as cylinders.
  svgPrototype["getBBox"] = function getBBox(this: Element) {
    return boxOf(this);
  };

  svgPrototype["getBoundingClientRect"] = function getBoundingClientRect(this: Element) {
    const box = boxOf(this);
    return {
      ...box,
      top: box.y,
      left: box.x,
      right: box.x + box.width,
      bottom: box.y + box.height,
      toJSON: () => box,
    };
  };

  svgPrototype["getComputedTextLength"] = function getComputedTextLength(this: Element) {
    return estimateTextWidth(this.textContent ?? "", fontSizeOf(this));
  };

  svgPrototype["getSubStringLength"] = function getSubStringLength(
    this: Element,
    start: number,
    length: number,
  ) {
    const text = (this.textContent ?? "").slice(start, start + length);
    return estimateTextWidth(text, fontSizeOf(this));
  };

  svgPrototype["getNumberOfChars"] = function getNumberOfChars(this: Element) {
    return (this.textContent ?? "").length;
  };

  svgPrototype["getScreenCTM"] = () => null;

  svgPrototype["getTotalLength"] = function getTotalLength(this: Element) {
    const box = boxOf(this);
    return box.width + box.height;
  };

  svgPrototype["getPointAtLength"] = function getPointAtLength(this: Element) {
    const box = boxOf(this);
    return { x: box.x, y: box.y };
  };
}

/**
 * Mermaid reads the DOM from module scope, so the jsdom window has to be visible as globals.
 * Only names Node does not already define are bridged, plus the handful Mermaid always needs.
 */
function bridgeGlobals(window: JSDOM["window"]): void {
  for (const key of Object.getOwnPropertyNames(window)) {
    if (key in globalThis) {
      continue;
    }
    try {
      Object.defineProperty(globalThis, key, {
        configurable: true,
        get: () => (window as unknown as LooseObject)[key],
      });
    } catch {
      // Some window properties refuse redefinition; Mermaid does not need them.
    }
  }

  Object.defineProperty(globalThis, "window", { configurable: true, get: () => window });
  Object.defineProperty(globalThis, "document", { configurable: true, get: () => window.document });
  Object.defineProperty(globalThis, "getComputedStyle", {
    configurable: true,
    get: () => window.getComputedStyle.bind(window),
  });
}

async function createEnvironment(): Promise<RenderEnvironment> {
  const dom = new JSDOM("<!DOCTYPE html><html><body></body></html>", {
    pretendToBeVisual: true,
    url: "http://localhost/",
  });
  const { window } = dom;

  installLayoutShims(window);
  bridgeGlobals(window);

  const mermaid = (await import("mermaid")).default;
  mermaid.initialize({
    startOnLoad: false,
    // Strict encodes HTML in labels and disables interaction callbacks.
    securityLevel: "strict",
    htmlLabels: false,
    flowchart: { htmlLabels: false },
    class: { htmlLabels: false },
    // Identical input must produce identical output so diagrams can be content-addressed.
    deterministicIds: true,
    suppressErrorRendering: true,
    maxTextSize: LIMIT_CEILINGS.maxInputBytes,
    maxEdges: 2000,
    logLevel: "fatal",
    fontFamily: '"trebuchet ms", verdana, arial, sans-serif',
  });

  return { window, mermaid };
}

let environment: Promise<RenderEnvironment> | null = null;

export function getEnvironment(): Promise<RenderEnvironment> {
  environment ??= createEnvironment();
  return environment;
}

/** Drops anything Mermaid left behind so one diagram cannot influence the next. */
export function resetDocument(window: JSDOM["window"]): void {
  window.document.body.replaceChildren();
}
