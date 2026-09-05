"""Ask answer citations → chapter-in-page anchors (feature 1)."""

from __future__ import annotations

import re
from pathlib import Path

from web.render import slugify_heading

MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)(?:#([^)]+))?\)")
FILE_RE = re.compile(r"\b(\d{2}_[\w.-]+\.md|index\.md|README\.md)\b")
HEADING_RE = re.compile(r"^#{1,3}\s+(.+)$", re.M)


def heading_slugs(text: str) -> list[dict]:
    items = []
    for match in HEADING_RE.finditer(text or ""):
        title = match.group(1).strip()
        items.append({"title": title, "id": slugify_heading(title)})
    return items


def chapter_href(tutorial_name: str, filename: str, anchor: str | None = None) -> str:
    base = f"/t/{tutorial_name}" if filename in {"index.md", "README.md"} else f"/t/{tutorial_name}/{filename}"
    if anchor:
        return f"{base}#{anchor}"
    return base


def extract_citations(
    answer: str,
    chapters: list,
    *,
    tutorial_name: str,
) -> list[dict]:
    """Pull markdown links and bare filenames from an Ask answer."""
    by_file = {getattr(ch, "filename", "") or ch.get("filename"): ch for ch in chapters}
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []

    def add(filename: str, title: str = "", anchor: str = "") -> None:
        key = (filename, anchor or "")
        if key in seen or filename not in by_file:
            return
        seen.add(key)
        ch = by_file[filename]
        ch_title = getattr(ch, "title", None) or (ch.get("title") if isinstance(ch, dict) else "") or title
        text = getattr(ch, "text", "") or ""
        if not anchor and title:
            for item in heading_slugs(text):
                if title in item["title"] or item["title"] in title:
                    anchor = item["id"]
                    break
        out.append(
            {
                "filename": filename,
                "title": ch_title or title or filename,
                "anchor": anchor,
                "href": chapter_href(tutorial_name, filename, anchor or None),
            }
        )

    for match in MD_LINK_RE.finditer(answer or ""):
        filename = Path(match.group(2).split("#", 1)[0]).name
        add(filename, title=match.group(1).strip(), anchor=(match.group(3) or "").strip())
    for filename in FILE_RE.findall(answer or ""):
        add(filename)
    return out


def used_chapter_citations(used_chapters: list[dict], tutorial_name: str, chapters: list | None = None) -> list[dict]:
    """Turn AskResult.used_chapters into jump links, preferring first H2."""
    by_file = {}
    for ch in chapters or []:
        name = getattr(ch, "filename", None) or (ch.get("filename") if isinstance(ch, dict) else None)
        if name:
            by_file[name] = ch
    items = []
    for ch in used_chapters or []:
        filename = ch.get("filename") or ""
        if not filename:
            continue
        anchor = ""
        raw = by_file.get(filename)
        text = getattr(raw, "text", "") if raw is not None else ""
        slugs = heading_slugs(text)
        if len(slugs) > 1:
            anchor = slugs[1]["id"]
        elif slugs:
            anchor = slugs[0]["id"]
        items.append(
            {
                "filename": filename,
                "title": ch.get("title") or filename,
                "anchor": anchor,
                "href": chapter_href(tutorial_name, filename, anchor or None),
            }
        )
    return items
