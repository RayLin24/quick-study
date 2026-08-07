"""通用兜底适配器：无语义选择器时依赖 <main>/<article>/<nav> 与 trafilatura 算法兜底。"""
from __future__ import annotations

from quickstudy.adapters.base import SiteAdapter


class GenericAdapter(SiteAdapter):
    name = "generic"
    content_selectors = ["main", "article", "div[role=main]", "#content", ".content"]
    sidebar_selectors = ["nav", "aside"]
