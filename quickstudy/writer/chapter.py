"""分章写作（ADR-001：英进中出；ADR-006：摘要链）。

上下文包 = 大纲条目 + 全量源 chunk（去重，不做预算截断——宁多勿缺）
         + 本章 Demo（M3 已过校验）+ 前后章摘要 + glossary 相关子集。
章尾 chunks 注释是 L3 内容覆盖率的对账依据。
"""
from __future__ import annotations

import json
import logging
import re

from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts
from quickstudy.storage import Workspace

log = logging.getLogger(__name__)

_CHARS_PER_TOKEN = 2.5          # 中英混合粗略换算（成本估算用，保守偏高）


def _load_chunks(ws: Workspace, chunk_ids: list[str]) -> dict[str, dict]:
    out = {}
    wanted = set(chunk_ids)
    for jl in ws.path("chunks").glob("*.jsonl"):
        for line in jl.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            c = json.loads(line)
            if c["chunk_id"] in wanted:
                out[c["chunk_id"]] = c
    return out


def load_chunk_page_map(ws: Workspace) -> dict[str, str]:
    """chunk_id → page_id 全量映射（L3 覆盖率对账用）。"""
    m: dict[str, str] = {}
    for jl in ws.path("chunks").glob("*.jsonl"):
        page_id = jl.stem
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                m[json.loads(line)["chunk_id"]] = page_id
    return m


def build_chapter_context(ws: Workspace, chapter: dict, concepts: list[dict]
                          ) -> tuple[str, list[str], list[str]]:
    """组装源材料块（全量）。返回 (context_text, included_chunk_ids, dropped_chunk_ids)。

    只去重（跳过 duplicate_of 标记），不做预算截断；dropped 恒为空，
    保留三元组签名以兼容 L3 对账与既有调用方。
    """
    chunk_ids: list[str] = []
    for c in concepts:
        chunk_ids.extend(c.get("chunks", []))
    chunk_ids = list(dict.fromkeys(chunk_ids))
    chunks = _load_chunks(ws, chunk_ids)

    included: list[str] = []
    blocks: list[str] = []
    for cid in chunk_ids:
        c = chunks.get(cid)
        if c is None or c.get("duplicate_of"):
            continue
        blocks.append(f"### [chunk {cid}] {c.get('title', '')} / {c.get('section_path', '')}\n"
                      f"(源: {c['url']})\n{c['text']}")
        included.append(cid)
    return "\n\n".join(blocks), included, []


def _chapter_demos(ws: Workspace, concepts: list[dict]) -> list[dict]:
    """本章概念映射页面已产出的 Demo（只收 passed）。"""
    pages = {p for c in concepts for p in c.get("pages", [])}
    demos = []
    for report in ws.path("demos").glob("*/*/exec_report.json"):
        r = json.loads(report.read_text(encoding="utf-8"))
        if r.get("status") != "passed":
            continue
        page_id = report.parent.parent.name
        if page_id not in pages:
            continue
        files = []
        for f in sorted(report.parent.glob("*.py")):
            files.append({"path": f.name, "content": f.read_text(encoding="utf-8")})
        if files:
            demos.append({"name": r.get("name", "demo"), "url": r.get("url", ""),
                          "files": files,
                          "readme": (report.parent / "README.md").read_text(encoding="utf-8")
                          if (report.parent / "README.md").exists() else ""})
    return demos


def _glossary_subset(ws: Workspace, text_hint: str) -> dict:
    """本章相关术语子集：按在源材料中的出现过滤全表（不做数量截断）。"""
    glossary = ws.read_json("glossary.json") or {}
    terms = glossary.get("terms", {})
    hits = {t: e for t, e in terms.items() if t in text_hint}
    if len(hits) < 20:  # 源材料覆盖不足时带上 keep_english 的高优先级条目
        for t, e in terms.items():
            if e.get("keep_english"):
                hits.setdefault(t, e)
    return hits


async def _complete_text(llm: LLMGateway, user: str, max_tokens: int) -> str:
    """章节正文补全；K3 思考吃 max_tokens 导致截断时加倍预算重试一次（同 _complete_json 策略）。

    长文生成常超 180s 默认读取超时，章节调用放宽到 600s。
    """
    try:
        resp = await llm.complete("chapter", prompts.PROMPT_CHAPTER_VERSION,
                                  prompts.PROMPT_CHAPTER_SYSTEM, user,
                                  max_tokens=max_tokens, temperature=0.4, timeout=600.0)
        return resp.text
    except RuntimeError as e:
        if "max_tokens" not in str(e):
            raise
        log.warning("章节输出被截断，max_tokens 加倍重试")
        resp = await llm.complete(
            "chapter", prompts.PROMPT_CHAPTER_VERSION,
            prompts.PROMPT_CHAPTER_SYSTEM,
            user + "\n\n（上次输出被截断：请控制篇幅，确保六个小节完整收尾）",
            max_tokens=max_tokens * 2, temperature=0.4, use_cache=False, timeout=600.0)
        return resp.text


async def write_chapter(llm: LLMGateway, ws: Workspace, outline: dict,
                        graph: dict, chapter: dict,
                        prev_summary: str, next_summary: str) -> dict:
    """写一章。返回 {markdown, context_chunks, dropped_chunks, used_chunks, demo_count}。"""
    concepts = [graph["concepts"][i] for i in chapter["concept_ids"]]
    context, included, dropped = build_chapter_context(ws, chapter, concepts)
    demos = _chapter_demos(ws, concepts)
    glossary = _glossary_subset(ws, context)

    demo_text = "\n\n".join(
        f"### Demo: {d['name']}（已通过沙箱验证，逐行注释版，源: {d['url']}）\n"
        + "\n".join(f"```{f['path'].split('.')[-1]}\n{f['content']}\n```" for f in d["files"])
        + (f"\n原理说明: {d['readme'][:400]}" if d["readme"] else "")
        for d in demos) or "（本章无已验证 Demo）"

    glossary_text = "\n".join(
        f"- {t}: {e['translation']}{'（保留英文）' if e.get('keep_english') else ''}"
        for t, e in glossary.items())

    user = (
        f"手册：{outline['book_title']}\n"
        f"本章：第{chapter['no']}章《{chapter['title']}》｜难度 {'★' * chapter['difficulty']}｜"
        f"预计 {chapter['est_hours']} 小时\n"
        f"本章任务：{chapter['summary']}\n"
        f"前一章摘要：{prev_summary or '（这是第一章）'}\n"
        f"后一章预告：{next_summary or '（这是最后一章）'}\n\n"
        f"术语表（必须遵守）：\n{glossary_text}\n\n"
        f"官方原文材料（{len(included)} 段；只能依据这些写）：\n{context}\n\n"
        f"本章可用 Demo：\n{demo_text}")

    md = (await _complete_text(llm, user, max_tokens=12288)).strip()
    if md.startswith("```"):
        md = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", md, flags=re.S).strip()

    used = re.search(r"<!--\s*chunks:\s*([0-9a-f, \s]+)-->", md)
    used_chunks = [s.strip() for s in used.group(1).split(",") if s.strip()] if used else []
    return {"markdown": md, "context_chunks": included, "dropped_chunks": dropped,
            "used_chunks": used_chunks, "demo_count": len(demos),
            "glossary_subset": glossary}


def extract_chapter_summary(md: str, fallback: str = "") -> str:
    """摘要链：从成稿的「小结」节提取下章的承上摘要（零额外 LLM 成本）。"""
    m = re.search(r"^## 6\.\s*小结.*?\n(.*?)(?=^## |\Z)", md, flags=re.S | re.M)
    if m:
        text = re.sub(r"\s+", " ", m.group(1)).strip()
        if text:
            return text[:400]
    return fallback


def estimate_chapter_costs(ws: Workspace, outline: dict, graph: dict) -> dict:
    """成本闸门用：不调 LLM，按上下文包大小粗估每章 token 消耗。"""
    per_chapter = []
    for ch in outline["chapters"]:
        concepts = [graph["concepts"][i] for i in ch["concept_ids"]]
        context, included, _ = build_chapter_context(ws, ch, concepts)
        demos = _chapter_demos(ws, concepts)
        demo_chars = sum(len(f["content"]) for d in demos for f in d["files"])
        est_in = int((len(context) + demo_chars + 4000) / _CHARS_PER_TOKEN)  # +大纲/术语表开销
        est_out = 5000  # 一章中文正文的经验值
        per_chapter.append({"no": ch["no"], "title": ch["title"],
                            "context_chunks": len(included), "demos": len(demos),
                            "est_input_tokens": est_in, "est_output_tokens": est_out})
    return {"per_chapter": per_chapter,
            "total_est_input": sum(c["est_input_tokens"] for c in per_chapter),
            "total_est_output": sum(c["est_output_tokens"] for c in per_chapter),
            "note": f"粗略估算（{_CHARS_PER_TOKEN} 字符/token），输出按每章 5000 token 经验值"}


def chapter_filename(chapter: dict) -> str:
    slug = re.sub(r"[^\w一-鿿]+", "-", chapter["title"]).strip("-")[:40]
    return f"{chapter['no']:02d}-{slug}.md"
