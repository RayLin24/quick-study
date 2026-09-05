"""Local full-text search over tutorial titles and bodies (feature 21)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import HEADING_RE
from web.render import list_tutorials


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
            blob = f"{item['name']}\n{title}\n{text}".lower()
            if all(n in blob for n in needles):
                idx = blob.find(needles[0])
                snippet = text[max(0, idx) : max(0, idx) + 160].replace("\n", " ").strip()
                hits.append(
                    {
                        "tutorial": item["name"],
                        "filename": path.name,
                        "title": title,
                        "href": f"/t/{item['name']}" if path.name in {"index.md", "README.md"} else f"/t/{item['name']}/{path.name}",
                        "snippet": snippet,
                    }
                )
            if len(hits) >= limit:
                return hits
    return hits
