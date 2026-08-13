import { JSDOM } from "jsdom";

import type { DiagramError, RemovedItem, SanitizationReport } from "./types.ts";

const SVG_NAMESPACE = "http://www.w3.org/2000/svg";
const XLINK_NAMESPACE = "http://www.w3.org/1999/xlink";

/** Elements that are dropped with their entire subtree. */
const FORBIDDEN_ELEMENTS = new Set([
  "animate",
  "animatemotion",
  "animatetransform",
  "audio",
  "base",
  "button",
  "canvas",
  "discard",
  "embed",
  "foreignobject",
  "form",
  "handler",
  "iframe",
  "input",
  "link",
  "meta",
  "object",
  "script",
  "set",
  "textarea",
  "video",
]);

/** Attributes carrying a URL. Only same-document fragments survive. */
const URL_ATTRIBUTES = new Set(["href", "src", "from", "to", "values", "formaction", "action"]);

const UNSAFE_CSS_TOKEN = /(javascript|vbscript|data)\s*:|expression\s*\(|-moz-binding|behavior\s*:/gi;

let sharedWindow: JSDOM["window"] | null = null;

function windowFor(): JSDOM["window"] {
  sharedWindow ??= new JSDOM("<!DOCTYPE html><html><body></body></html>").window;
  return sharedWindow;
}

export type SanitizeOutcome =
  | { ok: true; svg: string; report: SanitizationReport }
  | { ok: false; error: DiagramError };

function sanitizeError(code: string, message: string): SanitizeOutcome {
  return { ok: false, error: { stage: "sanitize", code, message, line: null, column: null } };
}

class Tally {
  entries: Map<string, RemovedItem>;

  constructor() {
    this.entries = new Map();
  }

  record(name: string, reason: string): void {
    const key = `${name}\u0000${reason}`;
    const existing = this.entries.get(key);
    if (existing) {
      existing.count += 1;
      return;
    }
    this.entries.set(key, { name, reason, count: 1 });
  }

  list(): RemovedItem[] {
    return [...this.entries.values()].sort(
      (a, b) => a.name.localeCompare(b.name) || a.reason.localeCompare(b.reason),
    );
  }
}

function sanitizeCss(css: string): { css: string; changes: number } {
  let changes = 0;
  let result = css.replace(/@import[^;]*;?/gi, () => {
    changes += 1;
    return "";
  });

  result = result.replace(/url\(\s*(['"]?)([^)'"]*)\1\s*\)/gi, (whole, _quote: string, target: string) => {
    if (target.startsWith("#")) {
      return whole;
    }
    changes += 1;
    return "none";
  });

  result = result.replace(UNSAFE_CSS_TOKEN, () => {
    changes += 1;
    return "";
  });

  return { css: result, changes };
}

function isSafeReference(value: string): boolean {
  return value.trim().startsWith("#");
}

/**
 * Removes everything executable or externally referencing from an SVG document: scripts, SMIL
 * animation, embedded HTML, event handler attributes, non-fragment URLs and unsafe CSS.
 */
export function sanitizeSvg(input: string): SanitizeOutcome {
  if (input.trim().length === 0) {
    return sanitizeError("sanitize.no-svg-root", "The SVG document is empty");
  }

  const window = windowFor();
  const document = new window.DOMParser().parseFromString(input, "image/svg+xml");
  const root = document.documentElement;

  if (!root || root.localName.toLowerCase() === "parsererror") {
    return sanitizeError("sanitize.no-svg-root", "The SVG document is not well-formed XML");
  }
  if (root.localName.toLowerCase() !== "svg" || root.namespaceURI !== SVG_NAMESPACE) {
    return sanitizeError(
      "sanitize.no-svg-root",
      `Expected an <svg> root in the SVG namespace, found <${root.localName}>`,
    );
  }
  if (document.getElementsByTagName("parsererror").length > 0) {
    return sanitizeError("sanitize.no-svg-root", "The SVG document is not well-formed XML");
  }

  const removedElements = new Tally();
  const removedAttributes = new Tally();
  let modifiedStyleRules = 0;

  const walk = (element: Element): void => {
    for (const child of [...element.childNodes]) {
      if (child.nodeType === window.Node.COMMENT_NODE) {
        child.parentNode?.removeChild(child);
        continue;
      }
      if (child.nodeType === window.Node.PROCESSING_INSTRUCTION_NODE) {
        child.parentNode?.removeChild(child);
        continue;
      }
      if (child.nodeType !== window.Node.ELEMENT_NODE) {
        continue;
      }

      const childElement = child as Element;
      const name = childElement.localName.toLowerCase();

      if (FORBIDDEN_ELEMENTS.has(name)) {
        removedElements.record(name, "forbidden-element");
        childElement.remove();
        continue;
      }
      if (childElement.namespaceURI !== SVG_NAMESPACE) {
        removedElements.record(name, "foreign-namespace");
        childElement.remove();
        continue;
      }

      if (name === "style") {
        const sanitized = sanitizeCss(childElement.textContent ?? "");
        if (sanitized.changes > 0) {
          childElement.textContent = sanitized.css;
          modifiedStyleRules += sanitized.changes;
        }
      }

      for (const attribute of [...childElement.attributes]) {
        const attributeName = attribute.localName.toLowerCase();
        const value = attribute.value;

        if (attributeName.startsWith("on")) {
          removedAttributes.record(attributeName, "event-handler");
          childElement.removeAttributeNS(attribute.namespaceURI, attribute.localName);
          continue;
        }
        if (
          (URL_ATTRIBUTES.has(attributeName) || attribute.namespaceURI === XLINK_NAMESPACE) &&
          !isSafeReference(value)
        ) {
          removedAttributes.record(attributeName, "external-reference");
          childElement.removeAttributeNS(attribute.namespaceURI, attribute.localName);
          continue;
        }
        if (attributeName === "style") {
          const sanitized = sanitizeCss(value);
          if (sanitized.changes > 0) {
            modifiedStyleRules += sanitized.changes;
            childElement.setAttribute("style", sanitized.css);
          }
          continue;
        }
        if (UNSAFE_CSS_TOKEN.test(value)) {
          UNSAFE_CSS_TOKEN.lastIndex = 0;
          removedAttributes.record(attributeName, "unsafe-value");
          childElement.removeAttributeNS(attribute.namespaceURI, attribute.localName);
          continue;
        }
        UNSAFE_CSS_TOKEN.lastIndex = 0;
      }

      walk(childElement);
    }
  };

  // The root element gets the same attribute treatment as its descendants.
  for (const attribute of [...root.attributes]) {
    const attributeName = attribute.localName.toLowerCase();
    if (attributeName.startsWith("on")) {
      removedAttributes.record(attributeName, "event-handler");
      root.removeAttributeNS(attribute.namespaceURI, attribute.localName);
    }
  }
  walk(root);

  const svg = new window.XMLSerializer().serializeToString(root);
  if (svg.trim().length === 0) {
    return sanitizeError("sanitize.empty-output", "Sanitization produced an empty document");
  }

  return {
    ok: true,
    svg,
    report: {
      removedElements: removedElements.list(),
      removedAttributes: removedAttributes.list(),
      modifiedStyleRules,
    },
  };
}
