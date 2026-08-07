"""覆盖率报告（design.md 4.1 / 6.1.4 / ADR-004）。

L1 覆盖率 = 解析成功页 / 发现页，附漏页清单与原因——不追求绝对 100%，
追求"每一页的取舍都有记录"。L2/L3 指标在 M2/M4 落地。
"""
from __future__ import annotations

from quickstudy.storage import Workspace, artifact_meta


def build_report(ws: Workspace, discovery, fetch_records: dict[str, dict],
                 parsed_docs: list[dict], duplicates: dict[str, str]) -> dict:
    pages = discovery.pages
    parsed_by_url = {d["url"]: d for d in parsed_docs}

    missing: list[dict] = []
    for p in pages:
        doc = parsed_by_url.get(p.url)
        if doc is None:
            rec = fetch_records.get(p.url, {})
            missing.append({"url": p.url,
                            "reason": rec.get("error") or rec.get("skipped") or "未抓取",
                            "sidebar_index": p.sidebar_index})
        elif not doc.get("ok"):
            missing.append({"url": p.url, "reason": doc.get("error", "解析失败"),
                            "sidebar_index": p.sidebar_index})

    # 硬错误：侧边栏页面抓取/解析失败（ADR-007 不对称告警的最终判定；
    # 有意过滤的 SKIP/范围排除不算漏页，列在 intentionally_filtered）
    hard = [m for m in missing if m["sidebar_index"] >= 0]

    n_ok = sum(1 for d in parsed_docs if d.get("ok"))
    n_final = len(pages)
    report = {
        "summary": {
            "discovered": n_final,
            "parsed_ok": n_ok,
            "coverage_l1": round(n_ok / n_final, 4) if n_final else 0.0,
            "duplicates": len(duplicates),
            "api_reference_pages": discovery.counts.get("api_reference", 0),
        },
        "coverage_definition": "L1 = 解析成功页 / 发现页（L2 概念覆盖率、L3 内容覆盖率在 M2/M4 落地）",
        "missing_pages": missing,
        "hard_alerts": hard + [{"url": a["url"], "reason": a["reason"]}
                               for a in discovery.alerts_hard],
        "soft_alerts": discovery.alerts_soft,
        "intentionally_filtered": discovery.filtered,
        "discovery_notes": discovery.notes,
        "scope_suggestion": discovery.scope,
        "quality_flags_top": _top_quality_flags(parsed_docs),
        "_meta": artifact_meta(discovered=n_final, parsed_ok=n_ok),
    }
    return report


def _top_quality_flags(parsed_docs: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in parsed_docs:
        for f in d.get("quality_flags", []):
            counts[f] = counts.get(f, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))
