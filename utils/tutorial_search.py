"""Local full-text search over tutorial titles and bodies (feature 21) + ranking (#34)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import HEADING_RE, SOURCE_RE
from web.render import list_tutorials

# title > source path > body
TITLE_W = 8
SOURCE_W = 4
BODY_W = 1


def rank_hit(title: str, sources: str, body: str, needles: list[str]) -> int:
    score = 0
    title_l = (title or "").lower()
    source_l = (sources or "").lower()
    body_l = (body or "").lower()
    for needle in needles:
        if needle in title_l:
            score += TITLE_W
        if needle in source_l:
            score += SOURCE_W
        if needle in body_l:
            score += BODY_W
    return score


def search_tutorials(output_dir: Path, query: str, *, limit: int = 30) -> list[dict]:
    q = (query or "").strip().lower()
    if not q:
        return []
    needles = [part for part in q.split() if part]
    hits = []
    for item in list_tutorials(output_dir):
        folder = Path(output_dir) / item["name"]
        for path in sorted(folder.glob("*.md")):
            text = path.read_text(encoding="utf-8")
            heading = HEADING_RE.search(text)
            title = heading.group(1).strip() if heading else path.stem
            sources = " ".join(SOURCE_RE.findall(text))
            blob = f"{item['name']}\n{title}\n{sources}\n{text}".lower()
            if all(n in blob for n in needles):
                score = rank_hit(title, sources, text, needles)
                idx = blob.find(needles[0])
                snippet = text[max(0, idx) : max(0, idx) + 160].replace("\n", " ").strip()
                hits.append(
                    {
                        "tutorial": item["name"],
                        "filename": path.name,
                        "title": title,
                        "href": f"/t/{item['name']}" if path.name in {"index.md", "README.md"} else f"/t/{item['name']}/{path.name}",
                        "snippet": snippet,
                        "score": score,
                    }
                )
    hits.sort(key=lambda h: (-int(h.get("score") or 0), h.get("filename") or ""))
    return hits[:limit]
