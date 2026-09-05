"""Beginner one-week reading path template (feature 44)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import _chapter_title


DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def week_path(folder: Path) -> dict:
    chapters = []
    for path in sorted(Path(folder).glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md", "heatmap.md"}:
            continue
        chapters.append({"filename": path.name, "title": _chapter_title(path.name, path.read_text(encoding="utf-8"))})
    days = []
    if not chapters:
        days = [{"day": DAYS[0], "chapters": [], "note": "还没有章节"}]
    else:
        n = max(1, (len(chapters) + 6) // 7)
        for i, label in enumerate(DAYS):
            chunk = chapters[i * n : (i + 1) * n]
            if not chunk and i > 0:
                continue
            days.append({"day": label, "chapters": chunk, "note": "精读并在编辑器打开 source"})
    return {"days": days, "chapter_count": len(chapters)}


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
