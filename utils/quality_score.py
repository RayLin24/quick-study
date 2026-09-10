"""Lightweight tutorial quality self-check (feature 8) + coverage/density (#33)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.ask_tutorial import SOURCE_RE, _chapter_title
from utils.dead_links import find_dead_markdown_links

SKIP = {"glossary.md", "heatmap.md"}
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")


def _content_chapters(root: Path) -> list[Path]:
    return [p for p in sorted(root.glob("*.md")) if p.name not in SKIP and p.name not in {"index.md", "README.md"}]


def abstraction_coverage(folder: Path) -> dict:
    """Share of named abstractions that appear in some chapter body."""
    root = Path(folder)
    meta = {}
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    names = []
    for item in meta.get("abstractions") or []:
        if isinstance(item, dict) and item.get("name"):
            names.append(str(item["name"]))
    chapters = _content_chapters(root)
    if not names:
        names = [_chapter_title(p.name, p.read_text(encoding="utf-8")) for p in chapters]
    blob = "\n".join(p.read_text(encoding="utf-8") for p in chapters).lower()
    covered = [name for name in names if name and name.lower() in blob]
    total = len(names) or 1
    ratio = len(covered) / total if names else 0.0
    return {"covered": len(covered), "total": len(names), "ratio": round(ratio, 4), "missing": [n for n in names if n not in covered]}


def relationship_density(folder: Path) -> dict:
    """Edges / possible directed pairs among content chapters."""
    root = Path(folder)
    chapters = _content_chapters(root)
    n = len(chapters)
    names = {p.name for p in chapters}
    edges: set[tuple[str, str]] = set()
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        details = (meta.get("relationships") or {}).get("details") or []
        ordered = [p.name for p in chapters]
        for rel in details:
            if not isinstance(rel, dict):
                continue
            src, dst = rel.get("from"), rel.get("to")
            if isinstance(src, int) and isinstance(dst, int) and 0 <= src < n and 0 <= dst < n and src != dst:
                edges.add((ordered[src], ordered[dst]))
    for path in chapters:
        for match in LINK_RE.finditer(path.read_text(encoding="utf-8")):
            dest = Path(match.group(1).split("#", 1)[0]).name
            if dest in names and dest != path.name:
                edges.add((path.name, dest))
    possible = max(1, n * (n - 1)) if n > 1 else 1
    density = (len(edges) / possible) if n > 1 else 0.0
    return {"edges": len(edges), "nodes": n, "possible": possible if n > 1 else 0, "density": round(density, 4)}


def score_tutorial(folder: Path) -> dict:
    root = Path(folder)
    chapters = [p for p in sorted(root.glob("*.md")) if p.name not in SKIP]
    issues: list[dict] = []
    empty = []
    no_source = []
    for path in chapters:
        text = path.read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.startswith("#")).strip()
        if len(body) < 40:
            empty.append(path.name)
            issues.append({"code": "empty_chapter", "file": path.name})
        if path.name not in {"index.md", "README.md"} and not SOURCE_RE.search(text):
            no_source.append(path.name)
            issues.append({"code": "no_source", "file": path.name})
    dead = find_dead_markdown_links(root)
    for item in dead:
        issues.append({"code": "dead_link", **item})
    coverage = abstraction_coverage(root)
    density = relationship_density(root)
    deductions = 15 * len(empty) + 10 * len(no_source) + 8 * len(dead)
    if coverage["total"] and coverage["ratio"] < 0.5:
        deductions += 6
        issues.append({"code": "low_abstraction_coverage", "ratio": coverage["ratio"]})
    if density["nodes"] > 1 and density["density"] < 0.05:
        deductions += 4
        issues.append({"code": "sparse_relationships", "density": density["density"]})
    score = max(0, 100 - deductions)
    return {
        "score": score,
        "ok": score >= 70,
        "chapters": len(chapters),
        "empty_chapters": empty,
        "missing_source": no_source,
        "dead_links": dead,
        "issues": issues,
        "abstraction_coverage": coverage,
        "relationship_density": density,
    }
