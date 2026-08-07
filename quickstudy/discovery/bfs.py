"""BFS 兜底发现（design.md 4.1.2）：导航侧边栏优先展开。

两种用法：
- sitemap 已给出全量清单时：只抓根页提取侧边栏顺序（主流生成器的侧边栏含全站目录，
  一页即可拿到完整 sidebar_index，无需全站 BFS）。
- sitemap 缺失/不全时：完整 BFS，边发现边抓取（抓到的 HTML 直接作为 raw 快照，
  通过 on_page 回调交给抓取阶段，避免二次请求）。
"""
from __future__ import annotations

import logging
from collections import deque
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from quickstudy.adapters.base import SiteAdapter
from quickstudy.urltools import UrlClass, normalize_url, same_site

log = logging.getLogger(__name__)


def extract_sidebar_order(html: str, base_url: str, adapter: SiteAdapter) -> dict[str, dict]:
    """从单页侧边栏提取 {url: {index, section}}。主流文档站侧边栏=全站目录。"""
    tree = HTMLParser(html)
    out: dict[str, dict] = {}
    for link in adapter.sidebar_links(tree, base_url):
        out.setdefault(link.url, {"index": link.index, "section": link.section})
    return out


def _body_links(tree: HTMLParser, base_url: str) -> list[str]:
    """侧边栏缺失时的降级：正文容器内链接（只作发现，不带顺序信息）。"""
    urls: list[str] = []
    for a in tree.css("main a[href], article a[href], body a[href]"):
        href = a.attributes.get("href", "")
        if href and not href.startswith(("#", "javascript:", "mailto:")):
            urls.append(normalize_url(urljoin(base_url, href)))
    return urls


async def bfs_crawl(root_url: str, fetcher, adapter: SiteAdapter, cfg,
                    on_page=None) -> tuple[list[dict], dict[str, dict]]:
    """完整 BFS。返回 (页面记录列表, 侧边栏顺序映射)。

    页面记录: {url, depth, via: sidebar|body, sidebar_index, section}
    """
    root_url = normalize_url(root_url)
    root_version = adapter.detect_version(root_url)
    visited: set[str] = {root_url}
    pages: list[dict] = [{"url": root_url, "depth": 0, "via": "root",
                          "sidebar_index": 0, "section": ""}]
    sidebar_order: dict[str, dict] = {}
    failures: list[str] = []
    frontier: deque[tuple[str, int]] = deque([(root_url, 0)])

    while frontier and len(pages) < cfg.max_pages:
        url, depth = frontier.popleft()
        result = await fetcher.fetch(url)
        if not result.ok:
            failures.append(f"{url}: {result.error or result.skipped or 'fetch failed'}")
            continue
        if on_page is not None:
            on_page(result)

        tree = HTMLParser(result.html.decode("utf-8", errors="replace"))
        sidebar = adapter.sidebar_links(tree, url)
        # 侧边栏优先 + 正文链接兜底（合并去重）：侧边栏可能只有版本/语言切换器，
        # 真实目录在正文里（如 docs.python.org 首页）
        candidates = [(l.url, "sidebar", l.index, l.section) for l in sidebar]
        seen_cand = {c[0] for c in candidates}
        for u in _body_links(tree, url):
            if u not in seen_cand:
                candidates.append((u, "body", -1, ""))

        for cand_url, via, sidx, section in candidates:
            if cand_url in visited or not same_site(cand_url, root_url):
                continue
            if adapter.classify(cand_url) == UrlClass.SKIP:
                continue
            cand_version = adapter.detect_version(cand_url)
            if cand_version and cand_version != root_version:
                continue  # 旧版本目录不展开（design.md 4.1）
            if not _within_prefixes(cand_url, cfg):
                continue
            if via == "sidebar":
                sidebar_order.setdefault(cand_url, {"index": sidx, "section": section})
            if depth + 1 > cfg.max_depth:
                continue
            visited.add(cand_url)
            pages.append({"url": cand_url, "depth": depth + 1, "via": via,
                          "sidebar_index": sidx, "section": section})
            frontier.append((cand_url, depth + 1))
            if len(pages) >= cfg.max_pages:
                break

    if failures:
        log.warning("BFS 抓取失败 %d 页（前5）: %s", len(failures), failures[:5])
    return pages, sidebar_order


def _within_prefixes(url: str, cfg) -> bool:
    path = urlparse(url).path
    if cfg.include_prefixes and not any(path.startswith(p) for p in cfg.include_prefixes):
        return False
    if cfg.exclude_prefixes and any(path.startswith(p) for p in cfg.exclude_prefixes):
        return False
    return True
