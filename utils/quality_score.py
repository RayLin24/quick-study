"""Lightweight tutorial quality self-check (feature 8)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import SOURCE_RE
from utils.dead_links import find_dead_markdown_links


def score_tutorial(folder: Path) -> dict:
    root = Path(folder)
    chapters = [p for p in sorted(root.glob("*.md")) if p.name not in {"glossary.md", "heatmap.md"}]
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
    deductions = 15 * len(empty) + 10 * len(no_source) + 8 * len(dead)
    score = max(0, 100 - deductions)
    return {
        "score": score,
        "ok": score >= 70,
        "chapters": len(chapters),
        "empty_chapters": empty,
        "missing_source": no_source,
        "dead_links": dead,
        "issues": issues,
    }
