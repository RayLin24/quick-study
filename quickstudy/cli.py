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
    return 1


if __name__ == "__main__":
    sys.exit(main())
