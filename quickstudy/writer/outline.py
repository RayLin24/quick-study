"""大纲生成（design.md 4.6.1 / ADR-002）：有序概念 → 目录大纲（大纲即契约）。"""
from __future__ import annotations

import logging

from quickstudy.knowledge.graph import _complete_json
from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts
from quickstudy.storage import Workspace, artifact_meta

log = logging.getLogger(__name__)


async def generate_outline(llm: LLMGateway, graph: dict, ordered: list[int],
                           violations: list[dict]) -> dict:
    """LLM 生成大纲；校验概念覆盖与编号合法性后落盘 outline.json。"""
    concepts = graph["concepts"]
    listing = "\n".join(
        f"[{ci}] {concepts[ci]['name']}：{concepts[ci]['description']}"
        f"（{len(concepts[ci].get('pages', []))} 页支撑）"
        for ci in ordered)
    site = graph.get("pages", [{}])[0].get("url", "") if graph.get("pages") else ""
    user = (f"目标站点：{site}\n读者：零基础初学者\n\n"
            f"已排序概念清单（顺序即学习路径，大纲须保持相邻归属）：\n{listing}")
    data = await _complete_json(llm, "outline", prompts.PROMPT_OUTLINE_VERSION,
                                prompts.PROMPT_OUTLINE_SYSTEM, user, max_tokens=8192)

    chapters = data.get("chapters", []) if isinstance(data, dict) else []
    n = len(concepts)
    problems: list[str] = []
    seen: dict[int, int] = {}
    valid_chapters = []
    for ch in chapters:
        ids = [i for i in ch.get("concept_ids", []) if isinstance(i, int) and 0 <= i < n]
        if not ids:
            problems.append(f"第{ch.get('no')}章无有效概念，剔除")
            continue
        for i in ids:
            seen.setdefault(i, ch.get("no"))
        valid_chapters.append({
            "no": int(ch.get("no", len(valid_chapters) + 1)),
            "title": str(ch.get("title", "")),
            "concept_ids": ids,
            "difficulty": int(ch.get("difficulty", 2)),
            "est_hours": float(ch.get("est_hours", 1.0)),
            "prereq": ch.get("prereq", []),
            "summary": str(ch.get("summary", "")),
        })

    uncovered = [ci for ci in range(n) if ci not in seen]
    if uncovered:
        problems.append(f"{len(uncovered)} 个概念未被大纲覆盖: "
                        f"{[concepts[i]['name'] for i in uncovered[:5]]}")
        log.warning("大纲覆盖缺口: %s", problems[-1])

    outline = {
        "book_title": str(data.get("book_title", "")),
        "chapters": valid_chapters,
        "concept_order": ordered,
        "uncovered_concepts": uncovered,
        "dependency_violations": violations,
        "problems": problems,
        "_meta": artifact_meta(),
    }
    return outline


def chapter_concepts(outline: dict, chapter: dict, graph: dict) -> list[dict]:
    return [graph["concepts"][i] for i in chapter["concept_ids"]]
