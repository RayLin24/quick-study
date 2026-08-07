"""站点适配器框架（design.md 4.1 / 6.1：站点指纹 → 适配器）。

把"任意技术文档站"收敛为"有限几种适配器 + 通用兜底"：
每个适配器提供内容容器选择器、侧边栏选择器、URL 过滤规则、版本目录规则、
api-reference 去向策略（ADR-007）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from quickstudy.urltools import UrlClass, classify_url, normalize_url


@dataclass
class SidebarLink:
    url: str
    index: int          # 侧边栏出现顺序（ADR-002：学习路径主干）
    section: str = ""   # 所属顶层分组（范围界定的信号之一）


class SiteAdapter:
    """适配器基类。选择器均为 CSS，按声明优先级尝试。"""

    name = "generic"
    # 主内容容器（design.md 4.2：规则优先）
    content_selectors: list[str] = ["main", "article", "div[role=main]"]
    # 导航侧边栏（文档站目录 = 全站结构最可靠信号源）
    sidebar_selectors: list[str] = ["nav", "aside"]
    # 噪音元素：抽取前剔除
    noise_selectors: list[str] = [
        "script", "style", "noscript", "header", "footer",
        ".cookie-banner", ".announce", ".md-clipboard", ".headerlink",
    ]
    # URL 过滤：None 表示不过滤（交由全局 classify_url）
    url_include: list[str] = []
    url_exclude: list[str] = []
    # api-reference 去向：keep（进正文）| appendix（可检索附录）| drop（ADR-007）
    api_reference_policy: str = "appendix"
    # 版本目录正则：命中则最后一个捕获组为版本号（如 /docs/5.3/...）。
    # 归一化后的 URL 无尾斜杠，模式必须兼容路径结尾。
    version_url_patterns: list[str] = [
        r"/(\d+\.\d+(?:\.\d+)?)(?:/|$)", r"/v(\d+(?:\.\d+)*)(?:/|$)",
        r"/(stable|latest|current|master|main|dev)(?:/|$)",
    ]
    # 版本切换器 DOM（用于识别"当前版本"）
    version_switcher_selector: str | None = None

    # ---- URL 治理 ----
    def classify(self, url: str) -> UrlClass:
        path = urlparse(url).path
        for pat in self.url_exclude:
            if re.search(pat, path, re.I):
                return UrlClass.SKIP
        for pat in self.url_include:
            if re.search(pat, path, re.I):
                return UrlClass.DOC
        return classify_url(url)

    def detect_version(self, url: str, tree: HTMLParser | None = None) -> str:
        """页面版本 tag（ADR-007：混版本 chunk 不合入同一 demo）。空串表示未识别。"""
        path = urlparse(url).path
        for pat in self.version_url_patterns:
            m = re.search(pat, path, re.I)
            if m:
                return (m.group(m.lastindex or 1)).lower()
        return ""

    # ---- DOM 抽取 ----
    def content_root(self, tree: HTMLParser):
        for sel in self.content_selectors:
            node = tree.css_first(sel)
            if node is not None:
                return node
        return tree.body

    def sidebar_links(self, tree: HTMLParser, base_url: str) -> list[SidebarLink]:
        """按文档序提取侧边栏链接；index 即学习路径主干顺序（ADR-002）。"""
        for sel in self.sidebar_selectors:
            nav = tree.css_first(sel)
            if nav is None:
                continue
            links, seen, idx = [], set(), 0
            for a in nav.css("a[href]"):
                href = a.attributes.get("href", "")
                if not href or href.startswith(("#", "javascript:")):
                    continue
                abs_url = normalize_url(urljoin(base_url, href))
                if abs_url in seen:
                    continue
                seen.add(abs_url)
                # 顶层分组：向上找最近的 li 的直属文本节点（粗略但够用）
                section = self._section_of(a)
                links.append(SidebarLink(url=abs_url, index=idx, section=section))
                idx += 1
            if links:
                return links
        return []

    @staticmethod
    def _section_of(a_node) -> str:
        node = a_node.parent
        for _ in range(4):  # 向上最多 4 层找分组标题
            if node is None:
                break
            if node.tag in ("ul", "nav", "aside", "div"):
                label = node.css_first(".sidebar-section, .md-nav__title, .menu__link--sublist, label")
                if label is not None:
                    text = label.text(strip=True)
                    if text:
                        return text
            node = node.parent
        return ""

    def strip_noise(self, root) -> None:
        for sel in self.noise_selectors:
            for node in root.css(sel):
                node.decompose()
