"""Docusaurus 适配器（LangChain 等 M4 目标站属此类，M1 提供基础实现）。"""
from __future__ import annotations

from quickstudy.adapters.base import SiteAdapter


class DocusaurusAdapter(SiteAdapter):
    name = "docusaurus"

    content_selectors = [
        "div.theme-doc-markdown", "article", "main",
    ]
    sidebar_selectors = [
        "nav.menu", "aside.theme-doc-sidebar-container", "nav",
    ]
    noise_selectors = SiteAdapter.noise_selectors + [
        ".theme-doc-footer", ".theme-doc-breadcrumbs", ".pagination-nav",
        ".theme-doc-toc-mobile", "div.theme-doc-version-banner",
    ]
    url_exclude = [r"/(blog|changelog|community|showcase)(/|$)"]
    # Docusaurus 版本目录：/docs/3.0.x/... 或 /docs/current/...（next 为未发布版）
    version_url_patterns = SiteAdapter.version_url_patterns + [r"/docs/(next)/"]
    version_switcher_selector = ".navbar__item.dropdown a"
