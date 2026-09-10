"""Ask-only SSE streaming (#22). Independent of pipeline LLM_STREAM billing."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

from utils.ask_citations import extract_citations, used_chapter_citations
from utils.ask_tutorial import (
    AskRefused,
    AskResult,
    ChapterDoc,
    _invoke_ask,
    build_ask_prompt,
    collect_tutorial_bundle,
    select_ask_chapters,
)
from utils.errors import format_error

# Ask SSE is a separate billed completion from generation (`LLM_STREAM`).
# One Ask stream = one chat.completions request. Do not also call blocking Ask
# unless the caller opts into fallback (extra billed request).
ASK_SSE_BILLING = (
    "Ask SSE 与流水线 LLM_STREAM 分开计费：一次流式提问 = 一次 chat.completions。"
    "不要在同一问再打一枪非流式，除非显式 fallback。"
)


def sse_line(payload: dict) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _finalize(answer: str, selected: list[ChapterDoc], chapters: list[ChapterDoc], tutorial_name: str) -> AskResult:
    used = [{"filename": ch.filename, "title": ch.title} for ch in selected]
    citations = extract_citations(answer, chapters, tutorial_name=tutorial_name)
    if not citations:
        citations = used_chapter_citations(used, tutorial_name, chapters)
    return AskResult(
        answer=answer,
        used_chapters=used,
        routed=True,
        top_k=len(selected),
        citations=citations,
        markdown=answer,
    )


def ask_tutorial_stream(
    folder: Path,
    question: str,
    *,
    call=None,
    token_iter: Callable[[str], Iterator[str]] | None = None,
) -> Iterator[dict]:
    """Yield Ask SSE events: meta / token / done. Mockable via token_iter or call."""
    q = (question or "").strip()
    if not q:
        raise AskRefused(format_error("请输入问题"))
    bundle = collect_tutorial_bundle(folder)
    chapters: list[ChapterDoc] = bundle["chapters"]
    selected, routed = select_ask_chapters(chapters, q)
    prompt = build_ask_prompt(bundle, q, selected)
    tutorial_name = Path(folder).name
    yield {
        "type": "meta",
        "routed": routed,
        "used_chapters": [{"filename": ch.filename, "title": ch.title} for ch in selected],
        "billing": ASK_SSE_BILLING,
        "surface": "ask",
    }
    parts: list[str] = []
    if token_iter is not None:
        for piece in token_iter(prompt):
            text = str(piece or "")
            if not text:
                continue
            parts.append(text)
            yield {"type": "token", "text": text}
        answer = "".join(parts)
    else:
        caller = call
        if caller is None:
            from utils.call_llm import call_llm

            caller = call_llm
        answer = _invoke_ask(caller, prompt)
        if answer:
            yield {"type": "token", "text": answer}
    result = _finalize(answer or "", selected, chapters, tutorial_name)
    yield {
        "type": "done",
        "answer": result.answer,
        "used_chapters": result.used_chapters,
        "citations": result.citations,
        "markdown": result.markdown,
        "routed": routed,
        "billing": ASK_SSE_BILLING,
    }
