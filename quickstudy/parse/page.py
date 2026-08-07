"""单页解析编排：HTML → parsed/{id}.md + parsed/{id}.json。"""
from __future__ import annotations

import re

from quickstudy.adapters.base import SiteAdapter
from quickstudy.parse.extract import extract_main
from quickstudy.parse.simhash import simhash
from quickstudy.parse.structure import extract_structure, to_markdown
from quickstudy.storage import artifact_meta

_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def detect_lang(text: str) -> str:
    """粗粒度语言检测：CJK 字符占比 > 10% 判 zh，否则 en（文档站多为英文）。"""
    sample = text[:5000]
    cjk = len(_CJK_RE.findall(sample))
    latin = len(_LATIN_RE.findall(sample))
    if cjk and cjk / max(cjk + latin, 1) > 0.1:
        return "zh"
    return "en"


def parse_page(page_id: str, url: str, html: str, adapter: SiteAdapter,
               root_url: str, version: str = "") -> tuple[str, dict]:
    """返回 (markdown 文本, parsed.json 结构)。"""
    tree, root, method = extract_main(html, adapter)

    if root is None:
        return "", {
            "id": page_id, "url": url, "ok": False, "error": "主内容抽取失败",
            "extraction_method": "none", "_meta": artifact_meta(url=url),
        }

    structure = extract_structure(root, url, root_url)
    md = to_markdown(root)

    # 侧边栏观测（ADR-002）：Docusaurus 等折叠式侧边栏需跨页合并才能还原全站目录顺序
    try:
        sidebar_observed = [{"url": l.url, "index": l.index, "section": l.section}
                            for l in adapter.sidebar_links(tree, url)][:200]
    except Exception:  # noqa: BLE001 - 侧边栏观测失败不影响正文解析
        sidebar_observed = []

    title = ""
    if structure["headings"]:
        h1 = next((h for h in structure["headings"] if h["level"] == 1), structure["headings"][0])
        title = h1["text"]
    if not title:
        title_node = tree.css_first("title")
        title = title_node.text(strip=True) if title_node else ""

    plain = root.text(separator=" ", strip=True)
    word_count = len(plain.split())
    quality = []
    if word_count < 80:
        quality.append("too_short")
    if not structure["headings"]:
        quality.append("no_headings")
    if method != "rules":
        quality.append(f"fallback_{method}")

    doc = {
        "id": page_id,
        "url": url,
        "ok": True,
        "title": title,
        "adapter": adapter.name,
        "version": version,
        "lang": detect_lang(plain),
        "word_count": word_count,
        "extraction_method": method,
        "quality_flags": quality,
        "simhash": format(simhash(plain), "016x"),
        "sidebar_observed": sidebar_observed,
        **structure,          # headings / code_blocks / tables / links / images
        "_meta": artifact_meta(url=url, adapter=adapter.name),
    }
    header = f"<!-- source: {url} | version: {version or '-'} | adapter: {adapter.name} -->\n\n"
    if title:
        header += f"# {title}\n\n"
    return header + md, doc
