"""Sitemap 发现引擎（design.md 4.1：覆盖率的确定性来源）。

- 探测顺序：robots.txt 声明的 sitemap → /sitemap.xml → /sitemap_index.xml
- 递归展开 sitemap index；支持 gzip；超阈值截断并记录
"""
from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET

from quickstudy.urltools import normalize_url

log = logging.getLogger(__name__)

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_MAX_SITEMAP_URLS = 50_000


async def discover_sitemap_urls(fetch_bytes, origin: str,
                                robots_sitemaps: list[str] | None = None) -> tuple[list[str], list[str]]:
    """返回 (页面URL清单, 诊断信息)。fetch_bytes: async callable(url) -> (status, bytes)。"""
    candidates = list(robots_sitemaps or [])
    candidates += [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]

    urls: list[str] = []
    notes: list[str] = []
    seen_maps: set[str] = set()

    async def expand(sitemap_url: str, depth: int = 0) -> None:
        if depth > 3 or sitemap_url in seen_maps or len(urls) >= _MAX_SITEMAP_URLS:
            return
        seen_maps.add(sitemap_url)
        try:
            status, body = await fetch_bytes(sitemap_url)
        except Exception as e:  # noqa: BLE001
            notes.append(f"sitemap 获取失败 {sitemap_url}: {e}")
            return
        if status != 200 or not body:
            notes.append(f"sitemap 不可用 {sitemap_url} (HTTP {status})")
            return
        if sitemap_url.endswith(".gz"):
            try:
                body = gzip.decompress(body)
            except OSError as e:
                notes.append(f"sitemap 解压失败 {sitemap_url}: {e}")
                return
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            notes.append(f"sitemap 解析失败 {sitemap_url}: {e}")
            return

        tag = root.tag
        if tag == f"{_SITEMAP_NS}sitemapindex" or tag == "sitemapindex":
            for loc in root.iter(f"{_SITEMAP_NS}loc"):
                if loc.text:
                    await expand(loc.text.strip(), depth + 1)
        else:  # urlset（含无命名空间的容错）
            locs = root.iter(f"{_SITEMAP_NS}loc") if _SITEMAP_NS in tag else root.iter("loc")
            for loc in locs:
                if loc.text and len(urls) < _MAX_SITEMAP_URLS:
                    urls.append(normalize_url(loc.text.strip()))

    for cand in candidates:
        await expand(cand)
        if urls:
            notes.append(f"sitemap 来源: {cand}（展开 {len(seen_maps)} 个文件）")
            break

    # 去重保序
    deduped = list(dict.fromkeys(urls))
    if len(deduped) != len(urls):
        notes.append(f"sitemap 去重 {len(urls) - len(deduped)} 条")
    return deduped, notes
