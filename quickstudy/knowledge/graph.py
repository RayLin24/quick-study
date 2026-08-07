"""知识图谱构建（design.md 4.3 / ADR-002、ADR-006）。

节点 = 概念（LLM 从页面摘要抽取）；边三类：
- 引用：页面间链接（解析阶段天然获得，确定性）→ 提升到概念间
- 同属：同一侧边栏分组（站点导航结构直接给出，确定性）→ 提升到概念间
- 依赖：LLM 分析概念间学习先后（唯一 LLM 边）

产物 graph.json：概念节点 + 三类边 + 概念↔chunk 双向映射（同时是溯源底座）。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from quickstudy.llm.gateway import LLMGateway, extract_json
from quickstudy.llm import prompts
from quickstudy.storage import Workspace, artifact_meta
from quickstudy.urltools import normalize_url

log = logging.getLogger(__name__)

_EXCERPT_CHARS = 400          # 页面摘要节选长度（喂给概念抽取的压缩包）
_MAX_DIGEST_PAGES = 400       # 超出则分片抽取后合并（M2 先不支持合并，截断并记录）


def _load_pages(ws: Workspace) -> list[dict]:
    """从 manifest + parsed json 汇总页面清单（仅解析成功页）。"""
    manifest = ws.read_json("manifest.json") or {}
    pages = []
    for url, entry in manifest.get("pages", {}).items():
        if not entry.get("parsed"):
            continue
        doc = ws.read_json(f"parsed/{entry['page_id']}.json") or {}
        pages.append({
            "page_id": entry["page_id"], "url": url,
            "title": doc.get("title", ""), "version": entry.get("version", ""),
            "sidebar_index": entry.get("sidebar_index", -1),
            "word_count": entry.get("word_count", 0),
            "links": [l["target"] for l in doc.get("links", [])],
            "headings": doc.get("headings", []),
        })
    pages.sort(key=lambda p: (p["sidebar_index"] < 0, p["sidebar_index"], p["url"]))
    return pages


def _page_excerpt(ws: Workspace, page_id: str) -> str:
    md = ws.path("parsed", f"{page_id}.md")
    if not md.exists():
        return ""
    text = md.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"```[\s\S]*?```", "[代码示例]", text)  # 代码块折叠为占位
    text = re.sub(r"<!--.*?-->", "", text)
    return text.strip()[:_EXCERPT_CHARS]


async def _complete_json(llm: LLMGateway, name: str, version: str, system: str,
                         user: str, max_tokens: int) -> dict | list:
    """LLM 调用 + JSON 解析；解析失败（思考型模型截断常见）加倍 max_tokens 重试一次。"""
    resp = await llm.complete(name, version, system, user, max_tokens=max_tokens)
    try:
        return extract_json(resp.text)
    except (ValueError, RuntimeError) as first_err:
        log.warning("%s 输出解析失败（%s），max_tokens 加倍重试", name, first_err)
        resp = await llm.complete(name, version,
                                  system + "\n\n注意：只输出 JSON，不要任何解释或思考过程。",
                                  user + "\n\n（上次输出被截断，请精简输出，只保留 JSON 本体）",
                                  max_tokens=max_tokens * 2)
        return extract_json(resp.text)


async def extract_concepts(llm: LLMGateway, digests: list[dict]) -> list[dict]:
    """LLM 概念抽取（PocketFlow IdentifyAbstractions 思路）。"""
    listing = "\n".join(
        f"[{i}] {d['title']} — {d['url']}\n    节选: {d['excerpt']}"
        for i, d in enumerate(digests))
    data = await _complete_json(
        llm, "concepts", prompts.PROMPT_CONCEPTS_VERSION,
        prompts.PROMPT_CONCEPTS_SYSTEM,
        f"以下是某技术文档站的 {len(digests)} 个页面摘要，请抽取核心概念：\n\n{listing}",
        max_tokens=16384)
    concepts = data.get("concepts", []) if isinstance(data, dict) else []

    valid: list[dict] = []
    n_pages = len(digests)
    for c in concepts:
        pages = sorted({int(i) for i in c.get("pages", [])
                        if isinstance(i, int) or str(i).isdigit()} & set(range(n_pages)))
        if not c.get("name"):
            continue
        valid.append({"name": str(c["name"]), "description": str(c.get("description", "")),
                      "digest_indices": pages})
    return valid


async def extract_dependencies(llm: LLMGateway, concepts: list[dict]) -> list[dict]:
    """LLM 依赖边抽取。"""
    listing = "\n".join(f"[{i}] {c['name']}：{c['description']}"
                        for i, c in enumerate(concepts))
    data = await _complete_json(
        llm, "relations", prompts.PROMPT_RELATIONS_VERSION,
        prompts.PROMPT_RELATIONS_SYSTEM,
        f"概念清单：\n{listing}", max_tokens=8192)
    edges = data.get("edges", []) if isinstance(data, dict) else []
    valid = []
    n = len(concepts)
    for e in edges:
        try:
            a, b = int(e["from"]), int(e["to"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= a < n and 0 <= b < n and a != b:
            valid.append({"from": a, "to": b, "reason": str(e.get("reason", ""))})
    return valid


def build_page_edges(pages: list[dict]) -> tuple[list[dict], dict[str, list[str]]]:
    """确定性边：引用（页面链接）+ 同属（侧边栏分组，由 manifest 观测合并）。"""
    url_to_idx = {p["url"]: i for i, p in enumerate(pages)}
    ref_edges: list[dict] = []
    for i, p in enumerate(pages):
        for target in p["links"]:
            j = url_to_idx.get(normalize_url(target))
            if j is not None and j != i:
                ref_edges.append({"from": i, "to": j, "type": "reference"})
    # 去重
    seen, deduped = set(), []
    for e in ref_edges:
        key = (e["from"], e["to"])
        if key not in seen:
            seen.add(key)
            deduped.append(e)
    return deduped, url_to_idx


def lift_to_concepts(page_edges: list[dict], concepts: list[dict],
                     pages: list[dict]) -> list[dict]:
    """页面边 → 概念边（概念含任一源页面且目标含任一目标页面即连）。"""
    page_to_concepts: dict[int, set[int]] = {}
    for ci, c in enumerate(concepts):
        for pi in c["digest_indices"]:
            page_to_concepts.setdefault(pi, set()).add(ci)

    concept_edges: dict[tuple[int, int], dict] = {}
    for e in page_edges:
        for ca in page_to_concepts.get(e["from"], ()):
            for cb in page_to_concepts.get(e["to"], ()):
                if ca != cb:
                    concept_edges.setdefault((ca, cb), {"from": ca, "to": cb,
                                                        "type": "reference", "weight": 0})
                    concept_edges[(ca, cb)]["weight"] += 1
    return list(concept_edges.values())


def map_concepts_to_chunks(ws: Workspace, concepts: list[dict],
                           digests: list[dict]) -> None:
    """概念↔chunk 双向映射（就地写入 concepts 的 pages/chunks 字段）。"""
    for c in concepts:
        c["pages"] = [digests[i]["page_id"] for i in c["digest_indices"]]
        c["urls"] = [digests[i]["url"] for i in c["digest_indices"]]
        chunks: list[str] = []
        for pid in c["pages"]:
            jl = ws.path("chunks", f"{pid}.jsonl")
            if jl.exists():
                for line in jl.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        chunks.append(json.loads(line)["chunk_id"])
        c["chunks"] = chunks


async def build_graph(ws: Workspace, llm: LLMGateway | None = None,
                      use_llm: bool = True) -> dict:
    """构建知识图谱。use_llm=False 时只产出确定性部分（页面图），供离线测试。"""
    pages = _load_pages(ws)
    if not pages:
        raise RuntimeError("manifest 中没有解析成功的页面，先跑 crawl")
    digests = [{**p, "excerpt": _page_excerpt(ws, p["page_id"])} for p in pages]
    if len(digests) > _MAX_DIGEST_PAGES:
        log.warning("页面数 %d 超过单次抽取上限 %d，截断（M4 再做分片合并）",
                    len(digests), _MAX_DIGEST_PAGES)
        digests = digests[:_MAX_DIGEST_PAGES]

    ref_edges, _ = build_page_edges(digests)
    graph: dict = {"pages": [{k: p[k] for k in ("page_id", "url", "title", "sidebar_index")}
                             for p in digests],
                   "page_reference_edges": ref_edges,
                   "concepts": [], "concept_edges": [], "_meta": artifact_meta()}

    if use_llm and llm is not None:
        concepts = await extract_concepts(llm, digests)
        log.info("概念抽取: %d 个概念 / %d 页", len(concepts), len(digests))
        dep_edges = await extract_dependencies(llm, concepts)
        concept_refs = lift_to_concepts(ref_edges, concepts, digests)
        map_concepts_to_chunks(ws, concepts, digests)

        # 覆盖审计：未归属任何概念的页面（L2 覆盖率诚实性的基础）
        covered = {i for c in concepts for i in c["digest_indices"]}
        uncovered = [digests[i]["url"] for i in range(len(digests)) if i not in covered]

        graph["concepts"] = concepts
        graph["concept_edges"] = (
            [{"from": e["from"], "to": e["to"], "type": "depends", "reason": e["reason"]}
             for e in dep_edges] + concept_refs)
        graph["uncovered_pages"] = uncovered
        graph["_meta"] = artifact_meta(n_pages=len(digests), n_concepts=len(concepts))

    ws.write_json("graph.json", graph)
    return graph
