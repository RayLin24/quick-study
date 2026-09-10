"""Validate Mermaid structure (AST-lite) and reject broken diagrams.

Used after generation so we can retry instead of silently falling back to
raw text in the reader.
"""

from __future__ import annotations

import re

FENCE_RE = re.compile(r"```mermaid\s*\n(.*?)```", re.IGNORECASE | re.DOTALL)
CLICK_RE = re.compile(r"^\s*click\s+(\S+)", re.IGNORECASE | re.MULTILINE)
NODE_DECL_RE = re.compile(
    r"(?m)^\s*(?:subgraph\s+)?([A-Za-z][\w-]*)\s*"
    r'(?:\[\[[^\]]*\]\]|\[[^\]]*\]|\([^)]*\)|\{[^}]*\}|>[^;]*|")'
)
# A0["label"] / A0[label] / A0(label)
NODE_INLINE_RE = re.compile(
    r"\b([A-Za-z][\w-]*)\s*(?:\[\[[^\]]*\]\]|\[[^\]]*\]|\([^)]*\)|\{[^}]*\})"
)
EDGE_RE = re.compile(
    r"([A-Za-z][\w-]*)\s+"
    r'(?:--\s*"[^"]*"\s+-->|--\s+\S+\s+-->|-->|---|-\.->|==>|--x|x--|o--)'
    r"\s*([A-Za-z][\w-]*)"
)
SEQ_ARROW_RE = re.compile(
    r"(?m)^\s*([A-Za-z][\w -]*?)\s*(?:->>|-->>|--x|-\)|-->>>|-->)\s*([A-Za-z][\w -]*?)\s*:"
)
SEQ_PARTICIPANT_RE = re.compile(
    r"(?mi)^\s*participant\s+(?:(\S+)\s+as\s+)?(.+?)\s*$"
)
KIND_RE = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram(?:-v2)?|erDiagram|mindmap)\b",
    re.IGNORECASE,
)


def extract_mermaid_blocks(text: str) -> list[str]:
    return [match.group(1).strip() for match in FENCE_RE.finditer(text or "")]


def _unbalanced(text: str, left: str, right: str) -> bool:
    return (text or "").count(left) != (text or "").count(right)


def parse_mermaid(source: str) -> dict:
    text = (source or "").strip()
    if text.startswith("```"):
        blocks = extract_mermaid_blocks(text if "```mermaid" in text else f"```mermaid\n{text}\n```")
        text = blocks[0] if blocks else text.strip("` \n")
    kind_match = KIND_RE.search(text)
    kind = (kind_match.group(1).lower() if kind_match else "flowchart")
    nodes: set[str] = set()
    edges: list[tuple[str, str]] = []
    clicks: list[str] = []

    if kind.startswith("sequence"):
        for match in SEQ_PARTICIPANT_RE.finditer(text):
            alias = (match.group(1) or match.group(2) or "").strip()
            if alias:
                nodes.add(alias)
        for match in SEQ_ARROW_RE.finditer(text):
            left = match.group(1).strip()
            right = match.group(2).strip()
            nodes.add(left)
            nodes.add(right)
            edges.append((left, right))
    else:
        for match in NODE_INLINE_RE.finditer(text):
            ident = match.group(1)
            if ident.lower() in {"flowchart", "graph", "subgraph", "end", "click", "style", "class", "classdef"}:
                continue
            nodes.add(ident)
        for match in EDGE_RE.finditer(text):
            edges.append((match.group(1), match.group(2)))
            nodes.add(match.group(1))
            nodes.add(match.group(2))
        clicks = [match.group(1) for match in CLICK_RE.finditer(text)]

    return {"kind": kind, "nodes": sorted(nodes), "edges": edges, "clicks": clicks, "source": text}


def validate_mermaid(source: str) -> dict:
    text = (source or "").strip()
    errors: list[str] = []
    warnings: list[str] = []
    if not text:
        return {"ok": False, "errors": ["empty"], "warnings": [], "nodes": [], "edges": []}
    if text.count('"') % 2:
        errors.append("unbalanced_quotes")
    if _unbalanced(text, "[", "]"):
        errors.append("unbalanced_brackets")
    parsed = parse_mermaid(text)
    nodes = set(parsed["nodes"])
    if not nodes and parsed["kind"] in {"flowchart", "graph"}:
        errors.append("no_nodes")
    for src, dst in parsed["edges"]:
        if src not in nodes or dst not in nodes:
            errors.append(f"orphan_edge:{src}->{dst}")
    for click_id in parsed.get("clicks") or []:
        if click_id not in nodes:
            warnings.append(f"click_unknown:{click_id}")
    if not KIND_RE.search(text):
        warnings.append("missing_diagram_kind")
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "nodes": parsed["nodes"],
        "edges": parsed["edges"],
        "kind": parsed["kind"],
    }


def validate_markdown_mermaids(markdown: str) -> dict:
    blocks = extract_mermaid_blocks(markdown or "")
    reports = [validate_mermaid(block) for block in blocks]
    return {
        "ok": all(item["ok"] for item in reports) if reports else True,
        "count": len(reports),
        "blocks": reports,
    }


def assert_mermaid_ok(source: str) -> dict:
    report = validate_mermaid(source)
    if not report["ok"]:
        raise ValueError(f"invalid mermaid: {', '.join(report['errors'])}")
    return report


def assert_chapter_mermaid(markdown: str) -> dict:
    report = validate_markdown_mermaids(markdown)
    if report["count"] and not report["ok"]:
        bad = [err for block in report["blocks"] for err in (block.get("errors") or [])]
        raise ValueError(f"invalid mermaid: {', '.join(bad) or 'compile failed'}")
    return report
