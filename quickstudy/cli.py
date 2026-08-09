"""CLI 入口：quickstudy crawl <root_url> [选项]。"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from quickstudy.config import TaskConfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="quickstudy",
                                description="技术文档站 → 中文学习手册（M1: 发现/爬取/解析）")
    sub = p.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="站点发现 + 全站爬取 + 结构化解析（M1）")
    crawl.add_argument("root_url")
    crawl.add_argument("--config", help="YAML 配置文件")
    crawl.add_argument("--workspace", default=None, help="工作区目录（默认 ./workspace）")
    crawl.add_argument("--max-pages", type=int, default=None)
    crawl.add_argument("--max-rps", type=float, default=None)
    crawl.add_argument("--incremental", action="store_true", default=None,
                       help="增量模式：只处理变化页")
    crawl.add_argument("--no-render", dest="render_escalation", action="store_false",
                       default=None, help="禁用 Playwright 渲染升级")
    crawl.add_argument("--include-prefix", action="append", dest="include_prefixes",
                       help="范围收窄：只收此前缀的路径（可多次）")
    crawl.add_argument("--exclude-prefix", action="append", dest="exclude_prefixes")
    crawl.add_argument("-v", "--verbose", action="store_true")

    org = sub.add_parser("organize", help="知识组织：切分/向量索引/知识图谱/术语表（M2）")
    org.add_argument("root_url", help="用于定位已有 workspace（同 crawl 的 URL）")
    org.add_argument("--workspace", default=None)
    org.add_argument("--no-llm", dest="use_llm", action="store_false", default=None,
                     help="只构建确定性部分（页面引用图），不调用 LLM")
    org.add_argument("--fake-embed", action="store_true", default=None,
                     help="强制使用假向量（无 DASHSCOPE_API_KEY 时的流程验证）")
    org.add_argument("-v", "--verbose", action="store_true")

    demo = sub.add_parser("demos", help="Demo 重构与沙箱校验（M3，需 Docker）")
    demo.add_argument("root_url")
    demo.add_argument("--workspace", default=None)
    demo.add_argument("--limit", type=int, default=None, help="处理候选数上限（默认 5）")
    demo.add_argument("--tech", default="python", help="沙箱技术栈（当前仅 python）")
    demo.add_argument("-v", "--verbose", action="store_true")

    out = sub.add_parser("outline", help="目录大纲生成 + 写作成本估算（M4 成本闸门）")
    out.add_argument("root_url")
    out.add_argument("--workspace", default=None)
    out.add_argument("-v", "--verbose", action="store_true")

    book = sub.add_parser("book", help="分章写作 + 质检 + VitePress 组装（M4，需先跑 outline）")
    book.add_argument("root_url")
    book.add_argument("--workspace", default=None)
    book.add_argument("--max-chapters", type=int, default=None, help="只写前 N 章（试跑用）")
    book.add_argument("--rewrite", action="store_true", help="忽略已有成稿重写")
    book.add_argument("--recheck-qc", action="store_true",
                      help="不调 LLM，对已有成稿离线重跑质检并回写报告")
    book.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "crawl":
        cfg = TaskConfig.load(
            args.root_url, config_path=args.config,
            workspace=args.workspace, max_pages=args.max_pages, max_rps=args.max_rps,
            incremental=args.incremental, render_escalation=args.render_escalation,
            include_prefixes=args.include_prefixes, exclude_prefixes=args.exclude_prefixes,
        )
        from quickstudy.pipeline import run_m1

        report = asyncio.run(run_m1(cfg))
        s = report["summary"]
        print("\n===== M1 完成 =====")
        print(f"发现 {s['discovered']} 页 / 解析成功 {s['parsed_ok']} 页 / "
              f"L1 覆盖率 {s['coverage_l1']:.1%} / 近重复 {s['duplicates']} 页")
        print(f"license: {report['license']['license']} | "
              f"版本: {report['versions'].get('site_version') or '-'}"
              f"{'（混版本!）' if report['versions'].get('mixed') else ''}")
        if report["hard_alerts"]:
            print(f"[!] 硬告警 {len(report['hard_alerts'])} 条（侧边栏页面缺失/被过滤），见 report.json")
        scope = report.get("scope_suggestion", {})
        if scope.get("mode") == "multi":
            print(f"[!] 范围界定: {scope.get('note', '')}")
        print(f"产物目录: {cfg.task_dir}")
        return 0 if not report["hard_alerts"] else 2

    if args.command == "organize":
        cfg = TaskConfig.load(args.root_url, workspace=args.workspace)
        from quickstudy.pipeline_m2 import run_m2

        out = asyncio.run(run_m2(cfg, use_llm=bool(args.use_llm is not False),
                                 fake_embed=bool(args.fake_embed)))
        g = out.get("graph", {})
        print("\n===== M2 完成 =====")
        print(f"chunks: {out['chunk_stats']['chunks']}（重复 {out['chunk_stats']['dup_chunks']}）"
              f" | 向量索引: {out['index']['chunks_indexed']}（{out['index']['embedder']}）")
        print(f"概念: {g.get('concepts', 0)} | 概念边: {g.get('concept_edges', 0)}"
              f" | 未覆盖页: {g.get('uncovered_pages', 0)}"
              f" | 术语: {out.get('glossary_terms', 0)}")
        print(f"产物目录: {cfg.task_dir}")
        return 0

    if args.command == "demos":
        cfg = TaskConfig.load(args.root_url, workspace=args.workspace)
        from quickstudy.pipeline_m3 import run_m3

        summary = asyncio.run(run_m3(cfg, limit=args.limit or 5, tech=args.tech))
        print("\n===== M3 完成 =====")
        print(f"Demo {summary['total']} 个 | 通过 {summary['passed']}"
              f"（一次过 {int(summary['pass_rate_first_try'] * 100)}% / 最终 {int(summary['pass_rate_final'] * 100)}%）"
              f" | 人工待办 {summary['manual_todo']} | 异常 {summary['errors']}"
              f" | 已注释 {summary['annotated']}")
        for r in summary["reports"]:
            mark = {"passed": "✓", "manual_todo": "!", "error": "✗"}.get(r["status"], "?")
            print(f"  [{mark}] {r['title']} / {r.get('section_path','')[:40]} "
                  f"({r['status']}, {r.get('rounds', 0)} 轮修复)")
        print(f"产物目录: {cfg.task_dir / 'demos'}")
        return 0 if summary["passed"] == summary["total"] else 2

    if args.command == "outline":
        cfg = TaskConfig.load(args.root_url, workspace=args.workspace)
        from quickstudy.pipeline_m4 import run_outline

        out = asyncio.run(run_outline(cfg))
        outline, est = out["outline"], out["estimate"]
        print("\n===== M4 大纲（成本闸门：确认后再跑 book） =====")
        print(f"《{outline['book_title']}》 共 {len(outline['chapters'])} 章"
              f" | 依赖违例 {len(outline['dependency_violations'])} 条"
              f" | 未覆盖概念 {len(outline['uncovered_concepts'])} 个")
        for ch in outline["chapters"]:
            e = next((c for c in est["per_chapter"] if c["no"] == ch["no"]), {})
            print(f"  {ch['no']:>2}. {ch['title']} （{'★' * ch['difficulty']}"
                  f" ~{ch['est_hours']}h, 概念 {len(ch['concept_ids'])} 个,"
                  f" 输入约 {e.get('est_input_tokens', 0) // 1000}k token）")
            print(f"      {ch.get('summary', '')}")
        if outline["problems"]:
            print("[!] 大纲问题: " + "；".join(outline["problems"]))
        print(f"\n写作成本粗估: 输入 ~{est['total_est_input'] // 1000}k + "
              f"输出 ~{est['total_est_output'] // 1000}k token（{est['note']}）")
        print("确认目录无误后执行: quickstudy book <同一 URL>")
        print(f"产物目录: {cfg.task_dir}")
        return 0

    if args.command == "book":
        cfg = TaskConfig.load(args.root_url, workspace=args.workspace)
        from quickstudy.pipeline_m4 import recheck_qc, run_book

        if args.recheck_qc:
            out = recheck_qc(cfg)
            print("\n===== QC 离线复核 =====")
            for m in out["chapters"]:
                if m["qc_errors"]:
                    print(f"  [!] 第{m['no']}章 {m['title']}: "
                          + "；".join(i["detail"][:60] for i in m["qc_errors"]))
            print(f"质检 error 章数: {out['qc_error_chapters']}/{len(out['chapters'])}")
            return 0 if not out["qc_error_chapters"] else 2

        out = asyncio.run(run_book(cfg, max_chapters=args.max_chapters,
                                   rewrite=args.rewrite))
        l3 = out["l3"]
        print("\n===== M4 成书完成 =====")
        for m in out["chapters"]:
            mark = "[!]" if m["qc_errors"] else "[ok]"
            print(f"  {mark} 第{m['no']}章 {m['title']} "
                  f"(chunks {m['used_chunks'] if isinstance(m['used_chunks'], int) else len(m['used_chunks'])}, "
                  f"demo {m['demo_count']}, 质检 error {len(m['qc_errors'])}/warn {len(m['qc_warnings'])})")
        print(f"L3 内容覆盖率: {l3['coverage_l3']:.1%}"
              f"（{l3['covered_pages']}/{l3['total_pages']} 页被章节溯源引用）")
        print(f"成书目录: {out['book']['book_dir']}（npm i && npm run docs:dev 预览）")
        return 0 if not any(m["qc_errors"] for m in out["chapters"]) else 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
