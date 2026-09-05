"""Smart 'next step' recommendation — not only sequential 1→N (feature 10)."""

from __future__ import annotations

from pathlib import Path

from utils.ask_tutorial import ChapterDoc, SOURCE_RE, _chapter_title, collect_tutorial_bundle


def _overlap(a: list[str], b: list[str]) -> int:
    left = {item.lower() for item in a}
    right = {item.lower() for item in b}
    score = len(left & right)
    for src in left:
        stem = Path(src.split("#", 1)[0]).stem.lower()
        if any(stem and stem in other for other in right):
            score += 1
    return score


def recommend_next(folder: Path, filename: str) -> dict:
    bundle = collect_tutorial_bundle(folder)
    chapters: list[ChapterDoc] = bundle["chapters"]
    current = next((ch for ch in chapters if ch.filename == filename), None)
    names = [ch.filename for ch in chapters if ch.filename not in {"index.md", "README.md"}]
    sequential = None
    if filename in names:
        idx = names.index(filename)
        if idx + 1 < len(names):
            nxt = next(ch for ch in chapters if ch.filename == names[idx + 1])
            sequential = {"filename": nxt.filename, "title": nxt.title, "reason": "顺序下一章"}
    if current is None:
        return {"current": filename, "recommended": sequential, "sequential": sequential, "candidates": []}

    scored = []
    for ch in chapters:
        if ch.filename in {filename, "index.md", "README.md"}:
            continue
        score = _overlap(current.sources, ch.sources)
        if current.title and current.title[:2] in ch.text:
            score += 2
        scored.append({"filename": ch.filename, "title": ch.title, "score": score, "sources": ch.sources[:4]})
    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0] if scored and scored[0]["score"] > 0 else None
    recommended = None
    if best:
        recommended = {
            "filename": best["filename"],
            "title": best["title"],
            "reason": "源文件重叠 / 正文互引" if best["score"] else "相关章",
            "score": best["score"],
        }
    elif sequential:
        recommended = sequential
    return {
        "current": filename,
        "recommended": recommended,
        "sequential": sequential,
        "candidates": scored[:3],
    }


def list_chapter_meta(folder: Path) -> list[dict]:
    root = Path(folder)
    items = []
    for path in sorted(root.glob("*.md")):
        if path.name in {"glossary.md", "heatmap.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        items.append(
            {
                "filename": path.name,
                "title": _chapter_title(path.name, text),
                "sources": SOURCE_RE.findall(text),
            }
        )
    return items
