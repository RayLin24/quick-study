"""Optional Ask evidence: short source-file windows from crawl_cache / local_dir.

Default OFF. Not a vector RAG stack — only *source: paths on Top-K chapters.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from utils.allow_dir import allow_dir_root
from utils.ask_retrieve import tokenize
from utils.crawl_cache import _norm_rel, load_cached_file_map

ASK_SOURCE_SNIPPETS_DEFAULT = False
ASK_SOURCE_SNIPPET_LINES = int(os.getenv("ASK_SOURCE_SNIPPET_LINES", "40"))
LAYER_TUTORIAL = "tutorial"
LAYER_SOURCE = "source"


def source_snippets_enabled(flag: bool | None = None) -> bool:
    if flag is not None:
        return bool(flag)
    raw = (os.getenv("ASK_SOURCE_SNIPPETS") or "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def snippet_line_budget(value: int | None = None) -> int:
    n = ASK_SOURCE_SNIPPET_LINES if value is None else int(value)
    return max(1, min(n, 200))


def load_tutorial_meta(folder: Path) -> dict:
    path = Path(folder) / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_rel(rel: str) -> str | None:
    text = _norm_rel(rel)
    if not text or text.startswith("/") or ":" in text.split("/", 1)[0]:
        return None
    parts = Path(text).parts
    if any(part in {"..", ""} for part in parts):
        return None
    return text


def _local_allowed(root: Path) -> bool:
    allow = allow_dir_root()
    if allow is None:
        return True
    try:
        root.resolve().relative_to(allow)
    except ValueError:
        return False
    return True


def _read_local(local_dir: str | Path | None, rel: str) -> str | None:
    if not local_dir:
        return None
    safe = _safe_rel(rel)
    if not safe:
        return None
    root = Path(local_dir).expanduser()
    if not root.is_dir() or not _local_allowed(root):
        return None
    candidate = (root / safe).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def resolve_source_text(
    rel: str,
    *,
    local_dir: str | Path | None = None,
    files_map: dict[str, str] | None = None,
    cache_map: dict[str, str] | None = None,
) -> str | None:
    safe = _safe_rel(rel)
    if not safe:
        return None
    if files_map:
        for key in (safe, rel, Path(safe).name):
            if key in files_map:
                return files_map[key]
    local = _read_local(local_dir, safe)
    if local is not None:
        return local
    mapping = cache_map if cache_map is not None else load_cached_file_map()
    return mapping.get(safe)


def _window_score(lines: list[str], query_tokens: set[str]) -> float:
    if not query_tokens:
        return 0.0
    toks = tokenize("\n".join(lines))
    return float(sum(1 for tok in toks if tok in query_tokens))


def best_line_window(text: str, question: str, max_lines: int) -> tuple[int, list[str]]:
    lines = (text or "").splitlines()
    budget = max(1, int(max_lines))
    if not lines:
        return 1, []
    if len(lines) <= budget:
        return 1, lines
    q_tokens = set(tokenize(question))
    best_i = 0
    best_score = -1.0
    step = max(1, budget // 2)
    last = len(lines) - budget
    for start in range(0, last + 1, step):
        window = lines[start : start + budget]
        score = _window_score(window, q_tokens)
        if score > best_score:
            best_score = score
            best_i = start
    if best_score <= 0:
        return 1, lines[:budget]
    return best_i + 1, lines[best_i : best_i + budget]


def collect_source_snippets(
    chapters: list,
    question: str,
    *,
    folder: Path | None = None,
    local_dir: str | Path | None = None,
    files_map: dict[str, str] | None = None,
    max_lines: int | None = None,
) -> list[dict]:
    """≤N lines per Top-K chapter, from that chapter's *source: paths only."""
    budget = snippet_line_budget(max_lines)
    meta = load_tutorial_meta(folder) if folder is not None else {}
    root = local_dir if local_dir is not None else meta.get("local_dir")
    cache_map = None if files_map is not None else load_cached_file_map()
    out: list[dict] = []
    for chapter in chapters:
        remaining = budget
        filename = getattr(chapter, "filename", "") or ""
        title = getattr(chapter, "title", "") or filename
        sources = [src for src in (getattr(chapter, "sources", None) or []) if src]
        ranked: list[tuple[float, str, str]] = []
        for src in sources:
            text = resolve_source_text(src, local_dir=root, files_map=files_map, cache_map=cache_map)
            if not text:
                continue
            ranked.append((_window_score(text.splitlines()[:80], set(tokenize(question))), src, text))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for _score, src, text in ranked:
            if remaining <= 0:
                break
            start, window = best_line_window(text, question, remaining)
            if not window:
                continue
            used = len(window)
            remaining -= used
            out.append(
                {
                    "path": _norm_rel(src),
                    "start_line": start,
                    "end_line": start + used - 1,
                    "lines": used,
                    "text": "\n".join(window),
                    "chapter": filename,
                    "chapter_title": title,
                    "layer": LAYER_SOURCE,
                }
            )
    return out


def format_source_evidence(snippets: list[dict]) -> str:
    if not snippets:
        return ""
    blocks = []
    for item in snippets:
        path = item.get("path") or ""
        start = item.get("start_line")
        end = item.get("end_line")
        chapter = item.get("chapter") or ""
        blocks.append(
            f"### source: {path} (L{start}-L{end}) · 来自 {chapter}\n{item.get('text') or ''}"
        )
    return "\n\n".join(blocks)


def tutorial_evidence(used_chapters: list[dict]) -> list[dict]:
    items = []
    for ch in used_chapters or []:
        row = dict(ch)
        row.setdefault("layer", LAYER_TUTORIAL)
        items.append(row)
    return items
