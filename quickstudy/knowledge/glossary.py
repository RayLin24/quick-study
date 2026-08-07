"""全局术语表（design.md 4.4 / ADR-001）。

术语一致性手册全任务共享一份 glossary：概念名 + 标题高频词 → LLM 定译名/保留英文。
人工覆盖层：workspace/{task}/glossary.override.json 存在时优先合并（人工改过的条目不被重生成冲掉）。
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter

from quickstudy.llm.gateway import LLMGateway
from quickstudy.llm import prompts
from quickstudy.storage import Workspace, artifact_meta

log = logging.getLogger(__name__)

_TERM_RE = re.compile(r"\b[A-Z][A-Za-z0-9.+#-]{1,}(?:[ \t]+[A-Z][A-Za-z0-9.+#-]+)*\b")
_COMMON_STOP = {"The", "This", "That", "You", "Your", "When", "What", "How", "Why",
                "API", "URL", "HTTP", "JSON", "HTML", "SQL", "OK",
                # admonition 标题与文档套话（MkDocs/Docusaurus 提示块）
                "Tip", "Note", "Warning", "Danger", "Important", "Info", "Details",
                "Example", "Check", "See", "New", "Optional", "Required",
                # 示例代码里的通用标识符，不是术语
                "None", "True", "False", "Item", "Items", "Data", "Result", "Main",
                "FastAPI App", "App"}


def collect_term_candidates(ws: Workspace, graph: dict, top_n: int = 80) -> list[str]:
    """候选术语：图谱概念名（英文部分）+ 页面标题/正文高频大写词。"""
    counter: Counter[str] = Counter()
    for c in graph.get("concepts", []):
        m = re.search(r"[（(]([^）)]+)[）)]", c.get("name", ""))  # 全/半角括号都认
        if m:
            counter[m.group(1)] += 10  # 概念原名加权
    for p in graph.get("pages", []):
        for token in _TERM_RE.findall(p.get("title", "")):
            if token not in _COMMON_STOP and len(token) > 2:
                counter[token] += 3
    # 正文高频（抽样前 60 页控制耗时）。
    # 单词级候选必须有"行内代码"佐证——先剔除围栏代码块（其中的英文注释会污染佐证集），
    # 否则句首大写虚词（But/And）会混进来
    for p in graph.get("pages", [])[:60]:
        md = ws.path("parsed", f"{p['page_id']}.md")
        if md.exists():
            text = md.read_text(encoding="utf-8", errors="replace")[:3000]
            body = re.sub(r"```[\s\S]*?```", " ", text)
            code_words = {w for seg in re.findall(r"`([^`\n]+)`", body)
                          for w in seg.split() if _TERM_RE.fullmatch(w)}
            for token in _TERM_RE.findall(body):
                if token in _COMMON_STOP or not (2 < len(token) < 30):
                    continue
                if " " not in token and token not in code_words:
                    continue
                counter[token] += 1
    # 阈值：标题(+3)/概念(+10)来源必然入选；仅正文出现的词需 ≥2 次防示例变量混入
    return [t for t, n in counter.most_common(top_n * 2)
            if n >= 2 and t not in _COMMON_STOP][:top_n]


async def build_glossary(ws: Workspace, llm: LLMGateway, graph: dict) -> dict:
    """生成 glossary.json；glossary.override.json 中的条目优先（人工覆盖）。"""
    candidates = collect_term_candidates(ws, graph)
    log.info("术语候选 %d 个", len(candidates))

    terms: dict[str, dict] = {}
    from quickstudy.knowledge.graph import _complete_json

    for i in range(0, len(candidates), 40):  # 每批 40 个，避免单响应对超长
        batch = candidates[i:i + 40]
        data = await _complete_json(
            llm, "glossary", prompts.PROMPT_GLOSSARY_VERSION,
            prompts.PROMPT_GLOSSARY_SYSTEM,
            "候选术语：\n" + "\n".join(f"- {t}" for t in batch),
            max_tokens=8192)
        for item in (data.get("terms", []) if isinstance(data, dict) else []):
            term = str(item.get("term", "")).strip()
            if term:
                terms[term] = {"translation": str(item.get("translation", "")),
                               "keep_english": bool(item.get("keep_english", False)),
                               "note": str(item.get("note", "")),
                               "source": "llm"}

    override_path = ws.path("glossary.override.json")
    if override_path.exists():
        overrides = json.loads(override_path.read_text(encoding="utf-8"))
        for term, entry in overrides.items():
            entry["source"] = "manual-override"
            terms[term] = entry
        log.info("合并人工覆盖术语 %d 条", len(overrides))

    glossary = {"terms": terms, "n_terms": len(terms), "_meta": artifact_meta(n=len(terms))}
    ws.write_json("glossary.json", glossary)
    return glossary
