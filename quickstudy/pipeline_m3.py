"""M3 流水线编排：片段选择 → 独立断言规格 → LLM 补全 → 沙箱执行 → 自愈 → 注释 → 入库。

通过率三指标（design.md 6.3.5）：一次通过率 / 修复后通过率 / 最终通过率。
"""
from __future__ import annotations

import asyncio
import logging
import re

from quickstudy.config import TaskConfig
from quickstudy.demo.annotate import annotate_demo
from quickstudy.demo.generate import (build_assertion_spec, extract_target_symbols,
                                      generate_demo)
from quickstudy.demo.repair import repair_loop, write_files
from quickstudy.demo.sandbox import docker_available
from quickstudy.demo.select import select_candidates
from quickstudy.llm.gateway import LLMGateway
from quickstudy.storage import Workspace, artifact_meta, now_iso

log = logging.getLogger(__name__)


def _page_excerpt(ws: Workspace, page_id: str, limit: int = 2000) -> str:
    md = ws.path("parsed", f"{page_id}.md")
    if not md.exists():
        return ""
    text = re.sub(r"```[\s\S]*?```", "[代码示例]", md.read_text(encoding="utf-8", errors="replace"))
    return text[:limit]


async def process_demo(llm: LLMGateway, ws: Workspace, candidate: dict) -> dict:
    """单个 Demo 的全生命周期。返回执行报告（同时落盘 exec_report.json）。"""
    demo_dir = ws.path("demos", candidate["page_id"], candidate["group_id"])
    demo_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"group_id": candidate["group_id"], "url": candidate["url"],
                    "title": candidate["title"], "section_path": candidate["section_path"],
                    "status": "failed", "rounds": 0, "annotated": False,
                    "at": now_iso()}

    excerpt = _page_excerpt(ws, candidate["page_id"])
    snippets = [b["code"] for b in candidate["blocks"]]
    target_symbols = extract_target_symbols(snippets)
    report["target_symbols"] = target_symbols

    try:
        # 1) 独立断言规格 → 2) 补全生成
        spec = await build_assertion_spec(llm, candidate, excerpt)
        demo = await generate_demo(llm, candidate, excerpt, spec)
        report["name"] = demo["name"]
        report["requirements"] = spec["requirements"]
        write_files(demo_dir, demo["files"])

        # 3) 沙箱执行 + 自愈（护栏在 repair_loop 内）
        result, history = await repair_loop(llm, demo, candidate, demo_dir, target_symbols)
        report["history"] = history
        report["rounds"] = len(history) - 1
        ok, reason = result.passed(demo.get("stdout_expect"))

        if not ok:
            report["status"] = "manual_todo"
            report["fail_reason"] = reason
            report["todo"] = "运行未通过且自愈耗尽轮次：需人工检查依赖/断言语义"
            (demo_dir / "TODO.md").write_text(
                f"# 人工待办\n\n- 来源: {candidate['url']}\n- 失败原因: {reason}\n"
                f"- 最后 stderr:\n```\n{result.stderr[-2000:]}\n```\n", encoding="utf-8")
        else:
            report["status"] = "passed"
            report["pass_at_round"] = len(history) - 1
            # 4) 注释后置（复跑校验在 annotate 内）
            ann = await annotate_demo(llm, demo, demo_dir)
            report["annotated"] = ann["annotated"]
            if ann["readme"]:
                (demo_dir / "README.md").write_text(ann["readme"], encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 单 demo 失败不阻断整批
        log.exception("Demo 处理异常: %s", candidate["group_id"])
        report["status"] = "error"
        report["fail_reason"] = f"{type(e).__name__}: {e}"

    ws.write_json(f"demos/{candidate['page_id']}/{candidate['group_id']}/exec_report.json",
                  {**report, "_meta": artifact_meta()})
    return report


async def run_m3(cfg: TaskConfig, limit: int = 5, tech: str = "python") -> dict:
    if not docker_available():
        raise RuntimeError("Docker 守护进程不可用：请先启动 Docker Desktop（M3 沙箱依赖）")
    ws = Workspace(cfg.task_dir)
    candidates = select_candidates(ws, tech=tech, limit=limit)
    log.info("Demo 候选: %d 组", len(candidates))
    if not candidates:
        raise RuntimeError("没有可选的代码片段组，先跑 crawl/organize")

    async with LLMGateway(ws.dir, model=cfg.llm_model) as llm:
        reports = []
        for c in candidates:  # 串行：沙箱资源有限，且便于观察成本
            log.info("Demo: %s | %s", c["title"], c["section_path"])
            reports.append(await process_demo(llm, ws, c))

        ws.write_json("llm_cost_m3.json", {
            "records": llm.ledger.records, "total_tokens": llm.ledger.total_tokens,
            "_meta": artifact_meta()})

    n = len(reports)
    passed = [r for r in reports if r["status"] == "passed"]
    first_try = sum(1 for r in passed if r.get("pass_at_round") == 0)
    summary = {
        "total": n,
        "passed": len(passed),
        "manual_todo": sum(1 for r in reports if r["status"] == "manual_todo"),
        "errors": sum(1 for r in reports if r["status"] == "error"),
        "pass_rate_first_try": round(first_try / n, 3) if n else 0,
        "pass_rate_final": round(len(passed) / n, 3) if n else 0,
        "annotated": sum(1 for r in passed if r["annotated"]),
        "reports": [{k: r[k] for k in ("group_id", "url", "title", "status",
                                       "rounds", "annotated") if k in r}
                    for r in reports],
    }
    report = ws.read_json("report.json") or {}
    report["m3"] = {"summary": {k: v for k, v in summary.items() if k != "reports"},
                    "demos": summary["reports"], "_meta": artifact_meta()}
    ws.write_json("report.json", report)
    return summary
