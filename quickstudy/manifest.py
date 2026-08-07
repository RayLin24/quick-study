"""manifest.json：全站 URL 清单 + 站点指纹 + 版本 + license + 增量元数据。

license 识别（ADR-007 / §9 合规落地）：页脚与 rel=license 链接扫描，
识别不出时在报告中标注风险，不静默放行。
"""
from __future__ import annotations

import re
from collections import Counter

from selectolax.parser import HTMLParser

from quickstudy.storage import Workspace, artifact_meta, now_iso

_LICENSE_PATTERNS: list[tuple[str, str]] = [
    (r"CC[ -]?BY[ -]?SA[ -]?(\d\.\d)", "CC-BY-SA"),
    (r"CC[ -]?BY[ -]?ND[ -]?(\d\.\d)", "CC-BY-ND"),
    (r"CC[ -]?BY[ -]?NC[ -]?(\d\.\d)", "CC-BY-NC"),
    (r"CC[ -]?BY[ -]?(\d\.\d)", "CC-BY"),
    (r"CC0", "CC0"),
    (r"Apache License,? (Version )?2\.0", "Apache-2.0"),
    (r"MIT License", "MIT"),
    (r"BSD [23][ -]Clause", "BSD"),
    (r"Mozilla Public License", "MPL"),
    (r"GNU (?:Free Documentation|General Public)", "GNU"),
]


def detect_license(html: str) -> dict:
    """从页脚/license 链接识别文档授权。返回 {license, evidence, risk}。"""
    tree = HTMLParser(html)
    evidence_text = ""
    for sel in ("footer", ".md-copyright", ".copyright", "[rel=license]"):
        node = tree.css_first(sel)
        if node is not None:
            evidence_text += " " + node.text(separator=" ", strip=True)
    for a in tree.css("a[rel=license], a[href*=creativecommons], a[href*=license]"):
        evidence_text += " " + (a.attributes.get("href") or "") + " " + a.text(strip=True)

    for pat, name in _LICENSE_PATTERNS:
        m = re.search(pat, evidence_text, re.I)
        if m:
            version = m.group(1) if m.groups() and m.group(1) else ""
            return {"license": f"{name}{'-' + version if version and name.startswith('CC') else ''}",
                    "evidence": m.group(0), "risk": "low"}
    if evidence_text.strip():
        return {"license": "unknown", "evidence": evidence_text.strip()[:200],
                "risk": "medium"}
    return {"license": "undetected", "evidence": "", "risk": "unknown"}


def summarize_versions(version_tags: list[str]) -> dict:
    """版本汇总：主版本 + 混版本提示（ADR-007：混版本 chunk 后续不合入同一 demo）。"""
    tags = [t for t in version_tags if t]
    if not tags:
        return {"site_version": "", "mixed": False, "distribution": {}}
    dist = Counter(tags)
    top, top_n = dist.most_common(1)[0]
    mixed = len(dist) > 1 and (len(tags) - top_n) / len(tags) > 0.1
    return {"site_version": top, "mixed": mixed,
            "distribution": dict(dist.most_common(10))}


class Manifest:
    """manifest.json 的读写封装；增量重跑时保留历史抓取元数据。"""

    def __init__(self, ws: Workspace):
        self.ws = ws
        self.data: dict = ws.read_json("manifest.json") or {
            "created_at": now_iso(), "pages": {}, "_meta": artifact_meta(),
        }
        self.data.setdefault("pages", {})

    def known_fetch_meta(self, url: str) -> dict | None:
        entry = self.data["pages"].get(url)
        if not entry:
            return None
        return {k: entry.get(k) for k in ("etag", "last_modified", "content_hash")}

    def update_page(self, url: str, **fields) -> None:
        self.data["pages"].setdefault(url, {}).update(fields)

    def save(self, **top_level) -> None:
        self.data.update(top_level)
        self.data["_meta"] = artifact_meta()
        self.ws.write_json("manifest.json", self.data)
