"""VitePress 适配器（基础实现）。"""
from __future__ import annotations

from quickstudy.adapters.base import SiteAdapter


class VitePressAdapter(SiteAdapter):
    name = "vitepress"

    content_selectors = ["div.VPDoc div.content-container", "main", "article"]
    sidebar_selectors = ["nav.VPSidebar", "aside.VPSidebar", "nav"]
    noise_selectors = SiteAdapter.noise_selectors + [
        ".VPDocFooter", ".VPLastUpdated", ".vp-doc .header-anchor",
    ]
