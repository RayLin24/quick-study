"""发现编排：Sitemap 优先 + 侧边栏顺序提取 + BFS 兜底，双引擎并集与不对称告警。

告警不对称（ADR-007）：
- 侧边栏有但最终清单没有（被分类/范围过滤掉）→ hard（真漏页风险）
- sitemap 有但侧边栏没有 → soft（隐藏页，照抓但标注）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import urlparse

from quickstudy.config import TaskConfig
from quickstudy.discovery.bfs import bfs_crawl, extract_sidebar_order
from quickstudy.discovery.scope import suggest_scope
from quickstudy.discovery.sitemap import discover_sitemap_urls
from quickstudy.urltools import UrlClass, normalize_url, same_site

log = logging.getLogger(__name__)


@dataclass
class DiscoveredPage:
    url: str
    url_class: str = "unknown"
    sources: list[str] = field(default_factory=list)   # sitemap / sidebar / bfs / root
    sidebar_index: int = -1                            # -1 = 不在侧边栏
    section: str = ""
    depth: int = 0


@dataclass
class DiscoveryResult:
    pages: list[DiscoveredPage] = field(default_factory=list)
    alerts_hard: list[dict] = field(default_factory=list)
    alerts_soft: list[dict] = field(default_factory=list)
    filtered: list[dict] = field(default_factory=list)   # 有意过滤（设计内排除），非告警
    scope: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    counts: dict = field(default_factory=dict)


async def discover(cfg: TaskConfig, fetcher, adapter, root_html: str,
                   on_page=None) -> DiscoveryResult:
    """on_page: BFS 模式下抓到页面的回调（raw 快照落盘），避免二次请求。"""
    result = DiscoveryResult()
    root_url = normalize_url(cfg.root_url)
    origin = f"{urlparse(root_url).scheme}://{urlparse(root_url).netloc}"
    # 版本锚定（design.md 4.1）：默认只抓与根页同版本的内容，旧版本仅记录不展开
    root_version = adapter.detect_version(root_url)

    # 1) robots → sitemap 种子（robots 策略在 fetcher 内部已缓存）
    policy = await fetcher.check_robots(root_url)

    async def fetch_bytes(url: str):
        r = await fetcher.fetch(url)
        return r.status, r.html

    sitemap_urls, notes = await discover_sitemap_urls(fetch_bytes, origin, policy.sitemaps)
    result.notes.extend(notes)

    # 2) 侧边栏顺序（ADR-002：学习路径主干）
    sidebar_order = extract_sidebar_order(root_html, root_url, adapter)

    pages: dict[str, DiscoveredPage] = {}

    def _scope_ok(url: str) -> bool:
        path = urlparse(url).path
        if cfg.include_prefixes and not any(path.startswith(p) for p in cfg.include_prefixes):
            return False
        if cfg.exclude_prefixes and any(path.startswith(p) for p in cfg.exclude_prefixes):
            return False
        return True

    def add(url: str, source: str, depth: int = 0) -> None:
        url = normalize_url(url)
        if not same_site(url, root_url):
            # 侧边栏/站点地图中的跨域外链（如 docs.python.org → devguide.python.org）不抓
            result.filtered.append({"url": url, "reason": "跨域外链", "via": source})
            return
        cls = adapter.classify(url)
        if cls == UrlClass.SKIP:
            result.counts["filtered_skip"] = result.counts.get("filtered_skip", 0) + 1
            # 有意过滤（blog/release-notes 等设计内排除），记录为信息而非告警
            result.filtered.append({"url": url, "reason": "SKIP 分类", "via": source})
            return
        url_version = adapter.detect_version(url)
        if url_version and url_version != root_version:
            result.counts["filtered_version"] = result.counts.get("filtered_version", 0) + 1
            result.filtered.append({"url": url, "reason": f"旧版本目录 v{url_version}（仅记录不展开）",
                                    "via": source})
            return
        if not _scope_ok(url):
            result.counts["filtered_scope"] = result.counts.get("filtered_scope", 0) + 1
            result.filtered.append({"url": url, "reason": "范围配置排除", "via": source})
            return
        page = pages.get(url)
        if page is None:
            page = DiscoveredPage(url=url, url_class=cls.value, depth=depth)
            pages[url] = page
        if source not in page.sources:
            page.sources.append(source)

    # 3) sitemap 清单入库
    for u in sitemap_urls:
        add(u, "sitemap")

    # 4) 侧边栏入库 + 顺序标注
    for u, info in sidebar_order.items():
        add(u, "sidebar")
        if u in pages:
            pages[u].sidebar_index = info["index"]
            pages[u].section = info["section"]

    # 5) sitemap 缺失/不足 → BFS 兜底（边发现边抓，HTML 复用为 raw 快照）
    if len(pages) < 10:
        result.notes.append(f"sitemap/侧边栏仅发现 {len(pages)} 页，启动 BFS 兜底")
        bfs_pages, bfs_sidebar = await bfs_crawl(root_url, fetcher, adapter, cfg, on_page)
        for rec in bfs_pages:
            add(rec["url"], "bfs", rec["depth"])
            if rec["via"] == "sidebar" and rec["url"] in pages:
                pages[rec["url"]].sidebar_index = rec["sidebar_index"]
                pages[rec["url"]].section = rec["section"]
        for u, info in bfs_sidebar.items():
            sidebar_order.setdefault(u, info)

    # 6) 软告警：sitemap 有但侧边栏没有（隐藏页，照抓但标注）
    if sidebar_order:
        for u, page in pages.items():
            if "sitemap" in page.sources and page.sidebar_index < 0:
                result.alerts_soft.append({"url": u, "note": "sitemap 有但侧边栏无（隐藏页）"})

    # 7) 范围界定建议（ADR-003）
    sections: dict[str, list[str]] = {}
    for u, page in pages.items():
        if page.section:
            sections.setdefault(page.section, []).append(u)
    result.scope = suggest_scope(list(pages.keys()), sections)

    # 8) 排序与截断：侧边栏序优先，其后 sitemap 序；超 max_pages 截断并记录
    ordered = sorted(pages.values(),
                     key=lambda p: (p.sidebar_index < 0, p.sidebar_index, p.url))
    if len(ordered) > cfg.max_pages:
        result.notes.append(f"发现 {len(ordered)} 页，超 max_pages={cfg.max_pages}，截断")
        ordered = ordered[: cfg.max_pages]
    result.pages = ordered
    result.counts.update({
        "sitemap": len(sitemap_urls),
        "sidebar": len(sidebar_order),
        "final": len(ordered),
        "api_reference": sum(1 for p in ordered if p.url_class == "api"),
    })
    return result
