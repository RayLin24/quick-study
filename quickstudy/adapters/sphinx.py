"""Sphinx 适配器（含 RTD/Furo/PyData 主题，基础实现）。"""
from __future__ import annotations

from quickstudy.adapters.base import SiteAdapter


class SphinxAdapter(SiteAdapter):
    name = "sphinx"

    content_selectors = [
        "div[role=main]", "main", "div.document div.body", "article.md-content__inner",
    ]
    sidebar_selectors = [
        "nav.bd-docs-nav",              # pydata-sphinx-theme
        "div.sphinxsidebar",            # 经典/RTD
        "nav.sidebar-drawer",           # furo
        "nav",
    ]
    noise_selectors = SiteAdapter.noise_selectors + [
        "div.related", "div.footer", ".sidebar-secondary", "a.headerlink",
    ]
    # Sphinx 站点的 genindex/py-modindex 是索引页非正文
    url_exclude = [r"/(genindex|py-modindex|search)(\.|/|$)"]
    version_url_patterns = SiteAdapter.version_url_patterns + [
        r"/(en|zh(?:-cn)?)/(\d+\.\d+[^/]*)/",  # RTD 风格 /en/stable/ 由基类规则覆盖
    ]
