"""In-chapter 'change this spot' exercises — no judge engine (feature 43)."""

from __future__ import annotations

import re
from pathlib import Path

from utils.ask_tutorial import SOURCE_RE, HEADING_RE


def chapter_exercises(text: str, *, filename: str = "") -> list[dict]:
    heading = HEADING_RE.search(text or "")
    title = heading.group(1).strip() if heading else filename or "本章"
    sources = SOURCE_RE.findall(text or "")
    items = []
    for idx, src in enumerate(sources[:3], start=1):
        items.append(
            {
                "id": idx,
                "prompt": f"打开 `{src}`，改这一处：让「{title}」的行为差一点点（例如加一行日志）。不用提交，也不判题。",
                "file": src,
                "filename": filename,
            }
        )
    if not items:
        items.append(
            {
                "id": 1,
                "prompt": f"在与「{title}」相关的源文件里加一行注释，标出你认为的入口。",
                "file": "",
                "filename": filename,
            }
        )
    return items


def tutorial_exercises(folder: Path) -> list[dict]:
    root = Path(folder)
    out = []
    for path in sorted(root.glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md", "heatmap.md"}:
            continue
        out.append({"filename": path.name, "items": chapter_exercises(path.read_text(encoding="utf-8"), filename=path.name)})
    return out
