"""范围界定（ADR-003）：一个根 URL 可能覆盖多个产品线，发现阶段后自动识别边界。

信号：sitemap URL 的路径首段聚类 + 侧边栏顶层分组。
输出建议：单产品（收窄前缀）/ 多产品（列候选，用户选择或拆多卷）/ 扁平站点（无需界定）。
"""
from __future__ import annotations

from collections import Counter
from urllib.parse import urlparse


def suggest_scope(urls: list[str], sidebar_sections: dict[str, list[str]] | None = None) -> dict:
    """urls: 已归一化的候选页面清单。sidebar_sections: {section: [url,...]}（可选）。

    返回 {mode, primary_prefix, candidates[], note}
    """
    seg_counter: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    for u in urls:
        segs = [s for s in urlparse(u).path.split("/") if s]
        first = segs[0] if segs else "(root)"
        seg_counter[first] += 1
        samples.setdefault(first, []).append(u)

    total = sum(seg_counter.values()) or 1
    candidates = [{"prefix": seg, "pages": cnt, "share": round(cnt / total, 3),
                   "sample": samples[seg][:3]}
                  for seg, cnt in seg_counter.most_common(12)]
    top = candidates[0] if candidates else None

    if not top or len(candidates) == 1:
        mode, primary = "flat", ""
        note = "站点路径结构扁平，无需范围界定"
    elif top["share"] >= 0.7:
        mode, primary = "single", f"/{top['prefix']}"
        note = f"路径首段 /{top['prefix']}/ 占 {top['share']:.0%}，可按单产品处理"
    else:
        mode, primary = "multi", ""
        note = ("检测到多个一级路径分区。若它们分属不同产品线（如 elastic.co/guide），"
                "建议用 include_prefixes 收窄或按 candidates 拆卷；"
                "若只是同一产品的章节分区（tutorial/advanced/...），可直接忽略本提示。")

    section_names = sorted((sidebar_sections or {}).keys())
    return {"mode": mode, "primary_prefix": primary, "candidates": candidates,
            "sidebar_top_sections": section_names, "note": note}
