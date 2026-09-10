"""Beginner one-week reading path template (feature 44) + adaptive (#35)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.ask_tutorial import _chapter_title

DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+\.md)\)")
SKIP = {"index.md", "README.md", "glossary.md", "heatmap.md"}


def _load_heatmap_edges(folder: Path) -> list[tuple[str, str]]:
    """Edges from meta.relationships or Markdown links between chapters."""
    edges: list[tuple[str, str]] = []
    meta_path = Path(folder) / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        rels = (meta.get("relationships") or {}).get("details") or meta.get("relationship_edges") or []
        names = []
        for path in sorted(Path(folder).glob("*.md")):
            if path.name not in SKIP:
                names.append(path.name)
        for rel in rels:
            if not isinstance(rel, dict):
                continue
            src, dst = rel.get("from"), rel.get("to")
            if isinstance(src, int) and isinstance(dst, int) and 0 <= src < len(names) and 0 <= dst < len(names):
                edges.append((names[src], names[dst]))
    for path in sorted(Path(folder).glob("*.md")):
        if path.name in SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        for match in LINK_RE.finditer(text):
            dest = Path(match.group(1).split("#", 1)[0]).name
            if dest.endswith(".md") and dest not in SKIP and dest != path.name:
                edges.append((dest, path.name))  # dest is prerequisite of path
    return edges


def _progress_read(progress: dict | None, tutorial: str) -> set[str]:
    read: set[str] = set()
    for raw in (progress or {}).keys():
        text = str(raw)
        if f"/t/{tutorial}/" not in text and not text.endswith(f"/t/{tutorial}"):
            continue
        tail = text.split("/t/", 1)[-1]
        parts = tail.split("/")
        if len(parts) >= 2 and parts[1].endswith(".md"):
            read.add(parts[1])
    return read


def topological_chapters(items: list[dict], edges: list[tuple[str, str]]) -> list[dict]:
    names = [item["filename"] for item in items]
    incoming = {name: 0 for name in names}
    outs: dict[str, list[str]] = {name: [] for name in names}
    for src, dst in edges:
        if src in incoming and dst in incoming and dst not in outs[src]:
            outs[src].append(dst)
            incoming[dst] += 1
    ready = [name for name in names if incoming[name] == 0]
    order = []
    seen = set()
    while ready:
        name = ready.pop(0)
        if name in seen:
            continue
        seen.add(name)
        order.append(name)
        for nxt in outs[name]:
            incoming[nxt] -= 1
            if incoming[nxt] <= 0:
                ready.append(nxt)
    for name in names:
        if name not in seen:
            order.append(name)
    by = {item["filename"]: item for item in items}
    return [by[name] for name in order if name in by]


def week_path(folder: Path, *, progress: dict | None = None, adaptive: bool = False) -> dict:
    chapters = []
    for path in sorted(Path(folder).glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md", "heatmap.md"}:
            continue
        chapters.append({"filename": path.name, "title": _chapter_title(path.name, path.read_text(encoding="utf-8"))})
    skipped: list[dict] = []
    if adaptive:
        edges = _load_heatmap_edges(folder)
        chapters = topological_chapters(chapters, edges)
        read = _progress_read(progress, Path(folder).name)
        kept, skipped = [], []
        for item in chapters:
            if item["filename"] in read:
                skipped.append(item)
            else:
                kept.append(item)
        chapters = kept
    days = []
    if not chapters:
        note = "已读完或还没有章节" if adaptive else "还没有章节"
        days = [{"day": DAYS[0], "chapters": [], "note": note}]
    else:
        n = max(1, (len(chapters) + 6) // 7)
        for i, label in enumerate(DAYS):
            chunk = chapters[i * n : (i + 1) * n]
            if not chunk and i > 0:
                continue
            days.append({"day": label, "chapters": chunk, "note": "精读并在编辑器打开 source"})
    return {
        "days": days,
        "chapter_count": len(chapters),
        "adaptive": bool(adaptive),
        "skipped": skipped,
    }


def week_path_markdown(folder: Path) -> str:
    data = week_path(folder)
    lines = ["# 新手一周读完", ""]
    for day in data["days"]:
        lines.append(f"## {day['day']}")
        for item in day["chapters"]:
            lines.append(f"- [{item['title']}]({item['filename']})")
        if not day["chapters"]:
            lines.append("- 复习 + 问这个仓")
        lines.append("")
    return "\n".join(lines)
