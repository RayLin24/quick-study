from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from utils.call_llm import call_llm
from utils.errors import format_error

INDEX_FILENAMES = ("index.md", "README.md")
SOURCE_RE = re.compile(
    r"(?:\#\s*source:|\*source:|\*\s*source:)\s*`?([^\s*`]+)`?",
    re.IGNORECASE,
)
HEADING_RE = re.compile(r"^#\s+(.+)$", re.M)
TOKEN_RE = re.compile(r"[A-Za-z0-9_\./-]+|[\u4e00-\u9fff]{1,}")

ASK_MAX_CHARS = int(os.getenv("ASK_MAX_CHARS", "24000"))
ASK_TOP_K = int(os.getenv("ASK_TOP_K", "4"))


class AskRefused(ValueError):
    """No generated tutorial (or empty question)."""


@dataclass
class ChapterDoc:
    filename: str
    title: str
    text: str
    sources: list[str]
    chars: int


@dataclass
class AskBundle:
    chapters: list[ChapterDoc]
    sources: list[str]
    files: list[str]
    markdown: str = ""


@dataclass
class AskResult:
    answer: str
    used_chapters: list[dict] = field(default_factory=list)
    routed: bool = False
    top_k: int = 0
    citations: list[dict] = field(default_factory=list)
    markdown: str = ""
    degraded: bool = False
    attempts: int = 1


def _tutorial_index(folder: Path) -> Path | None:
    for name in INDEX_FILENAMES:
        path = folder / name
        if path.is_file():
            return path
    return None


def _chapter_title(filename: str, text: str) -> str:
    match = HEADING_RE.search(text or "")
    if match:
        heading = re.sub(r"^Chapter\s+\d+\s*:\s*", "", match.group(1), flags=re.I).strip()
        if heading:
            return heading
    stem = Path(filename).stem
    return re.sub(r"^\d+_", "", stem).replace("_", " ").strip() or stem


def collect_tutorial_bundle(folder: Path) -> dict:
    root = Path(folder)
    index = _tutorial_index(root)
    if index is None:
        raise AskRefused(format_error("还没有生成这篇教程，无法提问。请先生成教程。"))
    chapters: list[ChapterDoc] = []
    sources: list[str] = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        found = [item.strip() for item in SOURCE_RE.findall(text) if item.strip()]
        sources.extend(found)
        chapters.append(
            ChapterDoc(
                filename=path.name,
                title=_chapter_title(path.name, text),
                text=text,
                sources=found,
                chars=len(text),
            )
        )
    unique_sources = sorted({item for item in sources if item})
    markdown = "\n\n".join(f"## file: {ch.filename}\n{ch.text}" for ch in chapters)
    bundle = AskBundle(
        chapters=chapters,
        sources=unique_sources,
        files=[ch.filename for ch in chapters],
        markdown=markdown,
    )
    return {
        "markdown": bundle.markdown,
        "sources": bundle.sources,
        "files": bundle.files,
        "chapters": chapters,
        "bundle": bundle,
    }


def _tokens(text: str) -> set[str]:
    return {part.lower() for part in TOKEN_RE.findall(text or "") if part.strip()}


def score_chapter(chapter: ChapterDoc, question: str) -> int:
    """Score by title + source path overlap. No embeddings."""
    needles = _tokens(question)
    if not needles:
        return 0
    title = (chapter.title or "").lower()
    filename = (chapter.filename or "").lower()
    sources = " ".join(chapter.sources).lower()
    score = 0
    for tok in needles:
        if tok in title:
            score += 5
        if tok in filename:
            score += 2
        if tok in sources:
            score += 4
        if any(tok in src.lower() for src in chapter.sources):
            score += 1
    return score


def select_ask_chapters(
    chapters: list[ChapterDoc],
    question: str,
    *,
    top_k: int | None = None,
    max_chars: int | None = None,
) -> tuple[list[ChapterDoc], bool]:
    """Pick chapters by title/source score. Never dump the whole book when over limit."""
    k = max(1, int(top_k if top_k is not None else ASK_TOP_K))
    budget = int(max_chars if max_chars is not None else ASK_MAX_CHARS)
    if not chapters:
        return [], False
    total = sum(ch.chars for ch in chapters)
    if total <= budget and len(chapters) <= k:
        return list(chapters), False

    scored = sorted(
        chapters,
        key=lambda ch: (
            score_chapter(ch, question),
            1 if ch.filename in INDEX_FILENAMES else 0,
            -ch.chars,
        ),
        reverse=True,
    )
    selected: list[ChapterDoc] = []
    used = 0
    for ch in scored:
        if len(selected) >= k:
            break
        if selected and used + ch.chars > budget:
            continue
        if not selected and ch.chars > budget:
            trimmed = ChapterDoc(
                filename=ch.filename,
                title=ch.title,
                text=ch.text[:budget],
                sources=ch.sources,
                chars=min(ch.chars, budget),
            )
            selected.append(trimmed)
            used += trimmed.chars
            break
        selected.append(ch)
        used += ch.chars
    if not selected:
        selected = [scored[0]]
    routed = True
    return selected, routed


def build_ask_prompt(bundle: dict, question: str, selected: list[ChapterDoc] | None = None) -> str:
    chapters = selected if selected is not None else bundle.get("chapters") or []
    toc_lines = []
    for ch in bundle.get("chapters") or []:
        toc_lines.append(f"- {ch.filename}: {ch.title}")
    toc = "\n".join(toc_lines) or "- （无章节）"
    source_set = sorted({src for ch in chapters for src in ch.sources}) or bundle.get("sources") or []
    source_lines = "\n".join(f"- {path}" for path in source_set) or "- （教程正文未标注 # source: 路径）"
    bodies = "\n\n".join(f"## file: {ch.filename}\n{ch.text}" for ch in chapters)
    return f"""你是 Quick Study 的教程问答助手。只能根据下面已经生成的教程 Markdown，以及其中标注的源码路径回答。

规则：
- 不要使用教程以外的知识，不要检索、不要编造仓库里没有出现的文件。
- 可以引用教程章节标题，以及文中的 `# source:` / `*source:` 路径。
- 如果教程里没有写到，明确回答「教程里没有提到」，不要猜测。
- 用与问题相同的语言回答。
- 下面「选中章节」才是正文；目录仅用于定位，不要把未选中的章当成已读。

全部章节目录：
{toc}

已标注的源码路径（选自本次纳入的章节）：
{source_lines}

选中章节正文：
{bodies}

问题：
{question}
"""


def _invoke_ask(caller, prompt: str):
    try:
        return caller(prompt, use_cache=False, temperature=0.2, stage="ask")
    except TypeError:
        return caller(prompt, use_cache=False, temperature=0.2)


def ask_tutorial_detailed(
    folder: Path,
    question: str,
    *,
    call=None,
    top_k: int | None = None,
    max_chars: int | None = None,
) -> AskResult:
    q = (question or "").strip()
    if not q:
        raise AskRefused(format_error("请输入问题"))
    bundle = collect_tutorial_bundle(folder)
    chapters: list[ChapterDoc] = bundle["chapters"]
    budget = int(max_chars if max_chars is not None else ASK_MAX_CHARS)
    k = top_k
    selected, routed = select_ask_chapters(chapters, q, top_k=k, max_chars=budget)
    prompt = build_ask_prompt(bundle, q, selected)
    caller = call or call_llm
    degraded = False
    attempts = 1
    try:
        answer = _invoke_ask(caller, prompt)
    except Exception as first:
        # Feature 3: shrink context and retry once.
        smaller = max(1200, budget // 3)
        selected, routed = select_ask_chapters(chapters, q, top_k=1, max_chars=smaller)
        prompt = build_ask_prompt(bundle, q, selected)
        try:
            answer = _invoke_ask(caller, prompt)
            degraded = True
            attempts = 2
        except Exception:
            raise first
    used = [{"filename": ch.filename, "title": ch.title} for ch in selected]
    from utils.ask_citations import extract_citations, used_chapter_citations

    citations = extract_citations(answer, chapters, tutorial_name=Path(folder).name)
    if not citations:
        citations = used_chapter_citations(used, Path(folder).name, chapters)
    return AskResult(
        answer=answer,
        used_chapters=used,
        routed=routed,
        top_k=len(selected),
        citations=citations,
        markdown=answer,
        degraded=degraded,
        attempts=attempts,
    )


def ask_tutorial(folder: Path, question: str, *, call=None) -> str:
    return ask_tutorial_detailed(folder, question, call=call).answer
