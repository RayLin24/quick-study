"""站点指纹检测（design.md 6.1.2）：识别文档生成器 → 加载对应适配器。

检测信号按可靠性排序：
1. <meta name="generator"> 内容（最可靠）
2. DOM 特征 class（次可靠，防 meta 被删）
3. 通用兜底
"""
from __future__ import annotations

from selectolax.parser import HTMLParser

from quickstudy.adapters.base import SiteAdapter
from quickstudy.adapters.docusaurus import DocusaurusAdapter
from quickstudy.adapters.generic import GenericAdapter
from quickstudy.adapters.mkdocs import MkDocsAdapter
from quickstudy.adapters.sphinx import SphinxAdapter
from quickstudy.adapters.vitepress import VitePressAdapter

_ADAPTERS: dict[str, type[SiteAdapter]] = {
    "mkdocs": MkDocsAdapter,
    "docusaurus": DocusaurusAdapter,
    "sphinx": SphinxAdapter,
    "vitepress": VitePressAdapter,
    "generic": GenericAdapter,
}

_GENERATOR_PATTERNS: list[tuple[str, str]] = [
    ("mkdocs-material", "mkdocs"),
    ("mkdocs", "mkdocs"),
    ("zensical", "mkdocs"),   # Zensical（MkDocs Material 团队新引擎）沿用 md-* DOM 结构
    ("docusaurus", "docusaurus"),
    ("sphinx", "sphinx"),
    ("vitepress", "vitepress"),
    ("gitbook", "generic"),   # GitBook 结构多变，先走兜底，后续单独适配
    ("antora", "generic"),
]

# DOM 特征：(CSS 选择器, 适配器名)。meta 缺失时的降级信号。
_DOM_PATTERNS: list[tuple[str, str]] = [
    ("div.md-container", "mkdocs"),
    ("nav.md-nav--primary", "mkdocs"),
    ("div.theme-doc-markdown", "docusaurus"),
    ("#__docusaurus", "docusaurus"),
    ("div.VPDoc", "vitepress"),
    ("div.sphinxsidebar", "sphinx"),
    ("nav.bd-docs-nav", "sphinx"),
]


def detect_fingerprint(html: str) -> dict:
    """返回 {adapter: str, generator: str, signal: meta|dom|fallback}。"""
    tree = HTMLParser(html)

    for meta in tree.css('meta[name="generator"]'):
        content = (meta.attributes.get("content") or "").lower()
        for pat, name in _GENERATOR_PATTERNS:
            if pat in content:
                return {"adapter": name, "generator": content, "signal": "meta"}

    for selector, name in _DOM_PATTERNS:
        if tree.css_first(selector) is not None:
            return {"adapter": name, "generator": "", "signal": "dom"}

    return {"adapter": "generic", "generator": "", "signal": "fallback"}


def get_adapter(name: str) -> SiteAdapter:
    return _ADAPTERS.get(name, GenericAdapter)()
