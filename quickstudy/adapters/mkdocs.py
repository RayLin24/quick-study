"""MkDocs / MkDocs Material 适配器（M1 完整实现，联调目标 FastAPI 即属此类）。"""
from __future__ import annotations

from quickstudy.adapters.base import SiteAdapter


class MkDocsAdapter(SiteAdapter):
    name = "mkdocs"

    # Material 主题结构：div.md-content 为正文，nav.md-nav--primary 为侧边栏
    content_selectors = [
        "div.md-content article.md-content__inner",  # Material
        "div.md-content",                            # Material 兜底
        "div[role=main] .col-md-9",                  # 经典 mkdocs 主题
        "main", "article",
    ]
    sidebar_selectors = [
        "nav.md-nav--primary",     # Material 左侧目录
        "ul.nav.nav-stacked",      # 经典主题
        "nav",
    ]
    noise_selectors = SiteAdapter.noise_selectors + [
        "nav.md-nav--secondary",   # 右侧页内 TOC
        ".md-sidebar--secondary",
        "footer.md-footer",
        ".md-source",              # "edit this page" 等
        "a.md-content__button",
    ]
    url_exclude = [r"/(changelog|blog|sponsor|insiders)(/|$)"]
    version_url_patterns = SiteAdapter.version_url_patterns + [r"^/(\d+\.\d+\.x)/"]
    version_switcher_selector = "div.md-version"
