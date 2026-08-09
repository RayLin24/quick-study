"""成稿质检关卡（design.md 4.6.3）：不过 LLM 的确定性检查 + L3 内容覆盖率。

检查项：固定结构完整性 / 占位符残留 / 术语表违规 / 溯源缺失。
L3 = 被章节实际溯源引用的页面 / 解析成功总页数（ADR-004）。
"""
from __future__ import annotations

import re

from quickstudy.storage import Workspace

_PLACEHOLDER_RE = re.compile(
    r"TODO|FIXME|TBD|待补充|待完善|此处省略|以下内容略|\bXXX{2,}\b", re.I)
# 注意：不收「占位符」——讲解模板引擎（Jinja 等）时它是合法技术词汇，必误报
_SECTION_RE = re.compile(r"^## ([1-6])\.\s*\S", re.M)
_LINK_RE = re.compile(r"\[[^\]]+\]\((https?://[^)]+)\)")
_CHUNKS_RE = re.compile(r"<!--\s*chunks:\s*([0-9a-f, \s]+)-->")


def check_chapter(md: str, *, context_chunks: list[str], used_chunks: list[str],
                  glossary_subset: dict) -> list[dict]:
    """单章质检。返回问题清单（level: error 必须返工 / warn 记入报告）。"""
    issues: list[dict] = []

    # 1) 固定结构：六个小节齐全
    found = sorted(set(_SECTION_RE.findall(md)))
    if found != ["1", "2", "3", "4", "5", "6"]:
        issues.append({"level": "error", "kind": "structure",
                       "detail": f"六段式结构缺失（仅有小节 {','.join(found) or '无'}）"})

    # 2) 占位符残留
    for line in md.splitlines():
        if _PLACEHOLDER_RE.search(line):
            issues.append({"level": "error", "kind": "placeholder",
                           "detail": f"疑似占位符残留: {line.strip()[:80]}"})

    # 3) 溯源：延伸阅读必须有官方链接；章末 chunks 注释必须存在且 ⊆ 上下文包
    if not _LINK_RE.search(md):
        issues.append({"level": "error", "kind": "trace",
                       "detail": "全文无任何官方来源链接（延伸阅读缺失）"})
    if not used_chunks:
        issues.append({"level": "error", "kind": "trace",
                       "detail": "缺少 <!-- chunks: ... --> 溯源注释"})
    else:
        unknown = set(used_chunks) - set(context_chunks)
        if unknown:
            issues.append({"level": "warn", "kind": "trace",
                           "detail": f"{len(unknown)} 个溯源 chunk 不在本章上下文包内: "
                                     f"{sorted(unknown)[:3]}"})

    # 4) 术语表违规（只查本章上下文包中出现过的术语）
    for term, entry in glossary_subset.items():
        zh = entry.get("translation", "")
        if entry.get("keep_english"):
            # 仅当"中文译名出现而英文原形缺席"才算疑似被翻译；
            # 术语仅出现在上下文而正文未讨论，不是违规（误报大户）
            if zh and zh in md and not re.search(rf"\b{re.escape(term)}\b", md):
                issues.append({"level": "warn", "kind": "glossary",
                               "detail": f"保留英文术语「{term}」疑被翻译为「{zh}」"})
        else:
            en_count = len(re.findall(re.escape(term), md))
            if zh and en_count >= 4 and zh not in md:
                issues.append({"level": "warn", "kind": "glossary",
                               "detail": f"术语「{term}」出现 {en_count} 次但译名「{zh}」从未使用"})
    return issues


def l3_coverage(ws: Workspace, chapters_written: list[dict]) -> dict:
    """L3 内容覆盖率：被章节实际引用的源页面 / 解析成功总页数。"""
    from quickstudy.writer.chapter import load_chunk_page_map

    chunk_page = load_chunk_page_map(ws)
    manifest = ws.read_json("manifest.json") or {}
    all_pages = {e["page_id"]: url for url, e in manifest.get("pages", {}).items()
                 if e.get("parsed")}

    covered: set[str] = set()
    for ch in chapters_written:
        for cid in ch.get("used_chunks", []):
            pid = chunk_page.get(cid)
            if pid:
                covered.add(pid)
    uncovered = sorted(url for pid, url in all_pages.items() if pid not in covered)
    total = len(all_pages)
    return {"covered_pages": len(covered), "total_pages": total,
            "coverage_l3": round(len(covered) / total, 4) if total else 0.0,
            "uncovered_urls": uncovered}
