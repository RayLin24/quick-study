"""Demo 候选选择（design.md 4.5）：从解析产物中挑出值得补全的代码片段组。

策略：同页同章节的代码块聚成一组（一个知识点的片段序列）→ 过滤（语言/行数/重复页）
→ 按侧边栏序优先（教程顺序即知识点递进）。
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

_MIN_CODE_LINES = 3          # 组内合计最少行数
_LANGS = {"python", "py", ""}  # 无语言标签的块在文档站里多为示例主语言，保留


def select_candidates(ws, tech: str = "python", limit: int = 20,
                      only_sidebar: bool = True) -> list[dict]:
    """返回 Demo 候选清单：{group_id, page_id, url, title, section_path, blocks[], total_lines}"""
    manifest = ws.read_json("manifest.json") or {}
    pages = manifest.get("pages", {})
    sidebar_rank = {pid: meta.get("sidebar_index", 10 ** 6)
                    for pid, meta in
                    ((m.get("page_id"), m) for m in pages.values())}

    groups: dict[tuple[str, str], dict] = {}
    for json_path in sorted(ws.path("parsed").glob("*.json")):
        doc = json.loads(json_path.read_text(encoding="utf-8"))
        if not doc.get("ok") or doc.get("duplicate_of"):
            continue
        if only_sidebar and sidebar_rank.get(doc["id"], 10 ** 6) < 0:
            continue
        for block in doc.get("code_blocks", []):
            lang = (block.get("language") or "").lower()
            if lang not in _LANGS or block.get("lines", 0) < 1:
                continue
            key = (doc["id"], block.get("section_path", ""))
            g = groups.setdefault(key, {
                "group_id": f"{doc['id']}-{len(groups)}",
                "page_id": doc["id"], "url": doc["url"],
                "title": doc.get("title", ""), "version": doc.get("version", ""),
                "section_path": block.get("section_path", ""),
                "sidebar_index": sidebar_rank.get(doc["id"], 10 ** 6),
                "blocks": [], "total_lines": 0,
            })
            g["blocks"].append({"language": lang, "code": block["code"],
                                "lines": block["lines"]})
            g["total_lines"] += block["lines"]

    candidates = [g for g in groups.values() if g["total_lines"] >= _MIN_CODE_LINES]
    candidates.sort(key=lambda g: (g["sidebar_index"], -g["total_lines"]))
    return candidates[:limit]
