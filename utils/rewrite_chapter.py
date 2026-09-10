"""Productized single-chapter rewrite (#21). One LLM call, not full --resume."""

from __future__ import annotations

import json
import re
from pathlib import Path

from utils.ask_tutorial import HEADING_RE, SOURCE_RE, _chapter_title
from utils.call_llm import call_llm
from utils.resume import save_chapter

SKIP = {"index.md", "README.md", "glossary.md", "heatmap.md"}


def _meta_path(folder: Path) -> Path:
    return Path(folder) / "meta.json"


def load_meta(folder: Path) -> dict:
    path = _meta_path(folder)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_meta(folder: Path, meta: dict) -> None:
    _meta_path(folder).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def first_paragraph(text: str) -> str:
    lines = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or SOURCE_RE.search(stripped) or stripped.startswith("<!--"):
            if lines:
                break
            continue
        lines.append(stripped)
        if len(" ".join(lines)) > 280:
            break
    return " ".join(lines).strip()


def list_chapter_abstractions(folder: Path) -> list[dict]:
    """Chapters with editable abstraction descriptions (from meta or first paragraph)."""
    root = Path(folder)
    meta = load_meta(root)
    saved = {item.get("filename"): item for item in (meta.get("abstractions") or []) if isinstance(item, dict)}
    items = []
    for path in sorted(root.glob("*.md")):
        if path.name in SKIP:
            continue
        text = path.read_text(encoding="utf-8")
        prev = saved.get(path.name) or {}
        items.append(
            {
                "filename": path.name,
                "title": prev.get("name") or _chapter_title(path.name, text),
                "description": prev.get("description") or first_paragraph(text),
                "sources": [s.strip() for s in SOURCE_RE.findall(text) if s.strip()],
                "chars": len(text),
            }
        )
    return items


def _assert_chapter(folder: Path, filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not filename.endswith(".md") or filename in SKIP:
        raise ValueError("非法章节名")
    path = Path(folder) / name
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def build_rewrite_prompt(*, filename: str, text: str, description: str, title: str) -> str:
    return f"""你是 Quick Study 的单章重写器。只重写下面这一章，不要生成整本教程，不要改其他章。

规则：
- 保留原标题行（或以 `# Chapter N: {title}` 开头）。
- 保留所有 `*source:` / `# source:` 行和指向其他 `.md` 的 Markdown 链接。
- 用「更新后的抽象说明」作为本章要讲清的核心，比原文更准确时以说明为准。
- 使用与原文相同的语言。
- 只输出完整 Markdown 正文，不要解释过程。

章节文件：{filename}

更新后的抽象说明：
{description}

当前章节：
{text}
"""


def persist_abstraction(folder: Path, filename: str, *, name: str, description: str) -> None:
    meta = load_meta(folder)
    items = [item for item in (meta.get("abstractions") or []) if isinstance(item, dict) and item.get("filename") != filename]
    items.append({"filename": filename, "name": name, "description": description})
    items.sort(key=lambda item: item.get("filename") or "")
    meta["abstractions"] = items
    save_meta(folder, meta)


def rewrite_chapter(
    folder: Path,
    filename: str,
    description: str = "",
    *,
    call=None,
) -> dict:
    """Change abstraction description and regenerate one chapter. One LLM call."""
    path = _assert_chapter(folder, filename)
    text = path.read_text(encoding="utf-8")
    title = _chapter_title(filename, text)
    desc = (description or "").strip() or first_paragraph(text)
    if not desc:
        raise ValueError("请填写抽象说明后再重写这一章")
    prompt = build_rewrite_prompt(filename=filename, text=text, description=desc, title=title)
    caller = call or call_llm
    try:
        rewritten = caller(prompt, use_cache=False, temperature=0.2, stage="write")
    except TypeError:
        rewritten = caller(prompt)
    body = (rewritten or "").strip()
    if not body:
        raise ValueError("模型没有返回章节正文")
    if not HEADING_RE.search(body):
        body = f"# {title}\n\n{body}"
    sources = SOURCE_RE.findall(text)
    if sources and not SOURCE_RE.search(body):
        body = body.rstrip() + "\n\n" + "\n".join(f"*source: {src}*" for src in sources) + "\n"
    save_chapter(folder, filename, body)
    persist_abstraction(folder, filename, name=title, description=desc)
    return {
        "filename": filename,
        "title": title,
        "description": desc,
        "chars": len(body),
        "calls": 1,
        "cheaper_than_resume": True,
    }
