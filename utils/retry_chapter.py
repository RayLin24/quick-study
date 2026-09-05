"""Retry a single failed chapter (feature 18)."""

from __future__ import annotations

from pathlib import Path

from utils.resume import load_progress

MIN_CHARS = 80


def failed_chapters(folder: Path) -> list[dict]:
    root = Path(folder)
    progress = load_progress(root)
    saved = progress.get("chapters") or {}
    items = []
    for path in sorted(root.glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md", "heatmap.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        info = saved.get(path.name) or {}
        too_short = len(text) < MIN_CHARS
        marker = "（轻量总览模式" in text or "GENERATION_FAILED" in text
        if too_short or marker:
            items.append({"filename": path.name, "chars": len(text), "reason": "short" if too_short else "failed"})
        elif path.name not in saved and len(text) < 200:
            items.append({"filename": path.name, "chars": len(text), "reason": "untracked"})
    return items


def mark_chapter_for_retry(folder: Path, filename: str) -> dict:
    path = Path(folder) / filename
    if Path(filename).name != filename or not filename.endswith(".md"):
        raise ValueError("非法章节名")
    if not path.is_file():
        raise FileNotFoundError(filename)
    progress = load_progress(folder)
    (progress.get("chapters") or {}).pop(filename, None)
    from utils.resume import progress_path
    import json

    progress_path(folder).write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    bak = path.with_suffix(path.suffix + ".bak")
    bak.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    path.unlink()
    return {"filename": filename, "cleared": True}


def retry_payload(meta: dict, filename: str) -> dict:
    return {
        "source_type": "repo" if meta.get("repo_url") else "dir",
        "repo_url": meta.get("repo_url") or "",
        "local_dir": meta.get("local_dir") or "",
        "name": meta.get("name") or "",
        "language": meta.get("language") or "Chinese",
        "resume": True,
        "retry_chapter": filename,
    }
