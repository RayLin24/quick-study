"""主内容抽取（design.md 4.2）：规则优先（适配器容器选择器），trafilatura 算法兜底。"""
from __future__ import annotations

import logging

from selectolax.parser import HTMLParser, Node

from quickstudy.adapters.base import SiteAdapter

log = logging.getLogger(__name__)

_MIN_TEXT_CHARS = 200  # 规则路径抽出内容低于此长度视为失败，触发算法兜底


def extract_main(html: str, adapter: SiteAdapter) -> tuple[HTMLParser, Node | None, str]:
    """返回 (tree, 主内容节点, 抽取方式 rules|trafilatura|body-fallback)。"""
    tree = HTMLParser(html)
    root = adapter.content_root(tree)

    if root is not None and _text_len(root) >= _MIN_TEXT_CHARS:
        adapter.strip_noise(root)
        return tree, root, "rules"

    # 算法兜底
    try:
        import trafilatura

        extracted = trafilatura.extract(
            html, output_format="html", include_tables=True,
            include_images=True, favor_recall=True,
        )
    except Exception as e:  # noqa: BLE001
        log.debug("trafilatura 失败: %s", e)
        extracted = None

    if extracted:
        t2 = HTMLParser(extracted)
        node = t2.body or t2.css_first("html")
        if node is not None:
            return t2, node, "trafilatura"

    # 最终兜底：整个 body（仍比放弃强，报告里会按 extraction_method 标注）
    fallback = tree.body or tree.css_first("html")
    if fallback is not None:
        adapter.strip_noise(fallback)
    return tree, fallback, "body-fallback"


def _text_len(node: Node) -> int:
    return len(node.text(separator="", strip=True))
