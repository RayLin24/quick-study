"""M2 流水线编排：切分 → 向量索引 → 知识图谱 → 术语表（ADR-001/002/006）。"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from quickstudy.config import TaskConfig
from quickstudy.knowledge.chunking import chunk_workspace
from quickstudy.knowledge.glossary import build_glossary
from quickstudy.knowledge.graph import build_graph
from quickstudy.knowledge.index import ChunkIndex, DashScopeEmbedder, FakeEmbedder
from quickstudy.llm.gateway import LLMGateway
from quickstudy.storage import Workspace, artifact_meta

log = logging.getLogger(__name__)

_EMBED_TEXT_CHARS = 1500   # embedding 输入截断（控制成本，chunk 前部代表性足够）


def _load_all_chunks(ws: Workspace) -> list[dict]:
    chunks: list[dict] = []
    for jl in sorted(ws.path("chunks").glob("*.jsonl")):
        for line in jl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                chunks.append(json.loads(line))
    return chunks


async def run_m2(cfg: TaskConfig, use_llm: bool = True,
                 fake_embed: bool = False) -> dict:
    ws = Workspace(cfg.task_dir)
    out: dict = {}

    # 1) 结构化切分
    chunk_stats = chunk_workspace(ws.path("parsed"), ws.path("chunks"))
    log.info("切分完成: %d 页 → %d chunks（重复标记 %d）",
             chunk_stats["pages"], chunk_stats["chunks"], chunk_stats["dup_chunks"])
    out["chunk_stats"] = chunk_stats

    # 2) 向量索引（无百炼 key 时降级 FakeEmbedder 并明确标注——不作为正式产物）
    chunks = _load_all_chunks(ws)
    use_real_embed = not fake_embed and bool(os.environ.get("DASHSCOPE_API_KEY"))
    embedder = DashScopeEmbedder() if use_real_embed else FakeEmbedder()
    if not use_real_embed:
        log.warning("未配置 DASHSCOPE_API_KEY：使用 FakeEmbedder 建索引（仅供流程验证，无检索语义）")
    index = ChunkIndex(ws.path("qdrant"), embedder.dim)
    batch_size = 200
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embedder.embed([c["text"][:_EMBED_TEXT_CHARS] for c in batch])
        index.upsert_chunks(batch, vectors)
    out["index"] = {"chunks_indexed": index.count(),
                    "embedder": "dashscope" if use_real_embed else "fake"}
    index.client.close()   # 显式关闭，避免解释器退出时 __del__ 噪音
    log.info("向量索引: %d chunks (%s)", out["index"]["chunks_indexed"], out["index"]["embedder"])

    # 3) 知识图谱 + 术语表（LLM）
    async with LLMGateway(ws.dir, model=cfg.llm_model) as llm:
        graph = await build_graph(ws, llm, use_llm=use_llm)
        out["graph"] = {"pages": len(graph["pages"]),
                        "reference_edges": len(graph["page_reference_edges"]),
                        "concepts": len(graph.get("concepts", [])),
                        "concept_edges": len(graph.get("concept_edges", [])),
                        "uncovered_pages": len(graph.get("uncovered_pages", []))}
        log.info("图谱: %s", out["graph"])

        glossary = {}
        if use_llm and graph.get("concepts"):
            glossary = await build_glossary(ws, llm, graph)
            out["glossary_terms"] = glossary["n_terms"]
            log.info("术语表: %d 条", glossary["n_terms"])

        # 成本台账落盘（ADR-006 / design.md §9 预算熔断的数据源）
        ws.write_json("llm_cost.json", {
            "records": llm.ledger.records,
            "total_tokens": llm.ledger.total_tokens,
            "_meta": artifact_meta(),
        })

    # 4) 报告合并
    report = ws.read_json("report.json") or {}
    report["m2"] = {**out, "_meta": artifact_meta()}
    ws.write_json("report.json", report)
    return out
