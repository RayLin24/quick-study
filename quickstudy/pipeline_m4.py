"""M4 流水线编排：学习路径排序 → 大纲（成本闸门）→ 分章写作 → 质检 → L3 → 组装。

`run_outline` 只到大纲 + 成本估算为止（大纲即契约，人工确认后再烧写作 token）；
`run_book` 要求 outline.json 已存在，按摘要链串行写章，断点续跑跳过已成章。
"""
from __future__ import annotations

import logging
import re

from quickstudy.config import TaskConfig
from quickstudy.llm.gateway import LLMGateway
from quickstudy.storage import Workspace, artifact_meta
from quickstudy.writer.assemble import assemble_book
from quickstudy.writer.chapter import (_glossary_subset, build_chapter_context,
                                       chapter_filename, estimate_chapter_costs,
                                       extract_chapter_summary, write_chapter)
from quickstudy.writer.ordering import order_concepts
from quickstudy.writer.outline import generate_outline
from quickstudy.writer.qc import check_chapter, l3_coverage

log = logging.getLogger(__name__)


def _load_graph(ws: Workspace) -> dict:
    graph = ws.read_json("graph.json")
    if not graph or not graph.get("concepts"):
        raise RuntimeError("graph.json 缺失或无概念：先跑 organize（M2）")
    return graph


def _order(graph: dict) -> tuple[list[int], list[dict]]:
    sidebar = {p["page_id"]: p.get("sidebar_index", -1) for p in graph.get("pages", [])}
    depends = [e for e in graph.get("concept_edges", []) if e.get("type") == "depends"]
    return order_concepts(graph["concepts"], depends, sidebar)


async def run_outline(cfg: TaskConfig) -> dict:
    """大纲生成 + 成本估算（成本闸门：此步之后由人工决定是否跑 run_book）。"""
    ws = Workspace(cfg.task_dir)
    graph = _load_graph(ws)
    ordered, violations = _order(graph)
    log.info("学习路径: %d 概念 / %d 条依赖违例记录", len(ordered), len(violations))

    async with LLMGateway(ws.dir, model=cfg.llm_model) as llm:
        outline = await generate_outline(llm, graph, ordered, violations)
        ws.write_json("outline.json", outline)
        ws.write_json("llm_cost_m4_outline.json", {
            "records": llm.ledger.records, "total_tokens": llm.ledger.total_tokens,
            "_meta": artifact_meta()})

    estimate = estimate_chapter_costs(ws, outline, graph)
    report = ws.read_json("report.json") or {}
    report["m4"] = {"outline": {
        "book_title": outline["book_title"], "chapters": len(outline["chapters"]),
        "uncovered_concepts": len(outline["uncovered_concepts"]),
        "dependency_violations": len(violations), "problems": outline["problems"],
        "estimate": {k: estimate[k] for k in ("total_est_input", "total_est_output", "note")},
        "_meta": artifact_meta()}}
    ws.write_json("report.json", report)
    return {"outline": outline, "estimate": estimate}


async def run_book(cfg: TaskConfig, max_chapters: int | None = None,
                   rewrite: bool = False) -> dict:
    """分章写作 + 质检 + L3 + 组装。断点续跑：chapters/ 已有成稿且未指定 rewrite 则跳过。"""
    ws = Workspace(cfg.task_dir)
    outline = ws.read_json("outline.json")
    if not outline or not outline.get("chapters"):
        raise RuntimeError("outline.json 缺失：先跑 outline 并确认目录后再写书（成本闸门）")
    graph = _load_graph(ws)

    chapters = outline["chapters"][:max_chapters] if max_chapters else outline["chapters"]
    state = ws.read_json("chapters/state.json") or {"written": {}}
    written_meta: list[dict] = []
    prev_summary = ""

    async with LLMGateway(ws.dir, model=cfg.llm_model) as llm:
        for idx, ch in enumerate(chapters):
            fname = chapter_filename(ch)
            cached = state["written"].get(str(ch["no"]))
            if cached and not rewrite and ws.path("chapters", cached["filename"]).exists():
                log.info("第%d章已有成稿，跳过（--rewrite 可重写）", ch["no"])
                md = ws.path("chapters", cached["filename"]).read_text(encoding="utf-8")
                prev_summary = extract_chapter_summary(md, ch.get("summary", ""))
                written_meta.append(cached)
                continue

            next_summary = (chapters[idx + 1].get("summary", "")
                            if idx + 1 < len(chapters) else "")
            log.info("写作 第%d章《%s》（概念 %s）", ch["no"], ch["title"], ch["concept_ids"])
            result = await write_chapter(llm, ws, outline, graph, ch,
                                         prev_summary, next_summary)

            # 质检关卡：error 级问题不自动返工（省 token），诚实记入报告
            issues = check_chapter(result["markdown"],
                                   context_chunks=result["context_chunks"],
                                   used_chunks=result["used_chunks"],
                                   glossary_subset=result["glossary_subset"])
            errors = [i for i in issues if i["level"] == "error"]
            if errors:
                log.warning("第%d章质检 error × %d: %s", ch["no"], len(errors),
                            errors[0]["detail"])

            ws.write_text(f"chapters/{fname}", result["markdown"])
            prev_summary = extract_chapter_summary(result["markdown"], ch.get("summary", ""))
            meta = {"no": ch["no"], "title": ch["title"], "filename": fname,
                    "used_chunks": result["used_chunks"],
                    "dropped_chunks": len(result["dropped_chunks"]),
                    "demo_count": result["demo_count"],
                    "qc_errors": errors,
                    "qc_warnings": [i for i in issues if i["level"] == "warn"]}
            state["written"][str(ch["no"])] = meta
            ws.write_json("chapters/state.json", state)
            written_meta.append(meta)

        ws.write_json("llm_cost_m4_book.json", {
            "records": llm.ledger.records, "total_tokens": llm.ledger.total_tokens,
            "_meta": artifact_meta()})

    chapter_files = [{"chapter": ch, "filename": m["filename"]}
                     for ch, m in zip(chapters, written_meta)]
    l3 = l3_coverage(ws, [{"used_chunks": m.get("used_chunks", [])} for m in written_meta])
    book = assemble_book(ws, outline, chapter_files, written_meta)

    report = ws.read_json("report.json") or {}
    report.setdefault("m4", {})["book"] = {
        "chapters_written": len(written_meta),
        "qc_error_chapters": sum(1 for m in written_meta if m["qc_errors"]),
        "coverage_l3": l3["coverage_l3"],
        "l3": {k: l3[k] for k in ("covered_pages", "total_pages")},
        "uncovered_urls_l3": l3["uncovered_urls"],
        "book_dir": book["book_dir"], "_meta": artifact_meta()}
    ws.write_json("report.json", report)
    return {"chapters": written_meta, "l3": l3, "book": book}


def _used_chunks_from_md(md: str) -> list[str]:
    m = re.search(r"<!--\s*chunks:\s*([0-9a-f, \s]+)-->", md)
    return [s.strip() for s in m.group(1).split(",") if s.strip()] if m else []


def recheck_qc(cfg: TaskConfig) -> dict:
    """离线重跑质检（不调 LLM）：QC 规则迭代后对既有成稿批量复核并回写状态。"""
    ws = Workspace(cfg.task_dir)
    outline = ws.read_json("outline.json") or {}
    graph = _load_graph(ws)
    state = ws.read_json("chapters/state.json") or {"written": {}}
    n_errors = 0
    for meta in state["written"].values():
        ch = next((c for c in outline.get("chapters", []) if c["no"] == meta["no"]), None)
        md_path = ws.path("chapters", meta["filename"])
        if ch is None or not md_path.exists():
            continue
        md = md_path.read_text(encoding="utf-8")
        concepts = [graph["concepts"][i] for i in ch["concept_ids"]]
        context, included, _ = build_chapter_context(ws, ch, concepts)
        issues = check_chapter(md, context_chunks=included,
                               used_chunks=_used_chunks_from_md(md),
                               glossary_subset=_glossary_subset(ws, context))
        meta["qc_errors"] = [i for i in issues if i["level"] == "error"]
        meta["qc_warnings"] = [i for i in issues if i["level"] == "warn"]
        n_errors += bool(meta["qc_errors"])
    ws.write_json("chapters/state.json", state)

    report = ws.read_json("report.json") or {}
    book = report.get("m4", {}).get("book")
    if book:
        book["qc_error_chapters"] = n_errors
        book["_meta"] = artifact_meta()
        ws.write_json("report.json", report)
    return {"chapters": list(state["written"].values()), "qc_error_chapters": n_errors}
