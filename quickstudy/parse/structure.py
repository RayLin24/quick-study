"""结构化抽取（design.md 4.2）：标题树、代码块、表格、内部链接、图片。

这是与"普通网页转 Markdown"拉开差距的关键层：
- 代码块 → 独立实体（语言标签、所在章节路径、前后说明段落引用），供 M3 补全 Demo
- 表格 → Markdown + JSON 双份（参数表程序性注入，不许 LLM 自由发挥）
- 内部链接 → 页面间引用边（喂知识图谱）
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser, Node

from quickstudy.urltools import normalize_url, same_site

_HEADING_TAGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}


def walk_subtree(node: Node):
    """子树遍历（含自身）。selectolax 的 traverse() 会越界到后续兄弟节点，不可用。"""
    yield node
    for child in node.iter():
        yield from walk_subtree(child)


def extract_structure(root: Node, page_url: str, root_url: str) -> dict:
    """root: 已去噪的主内容容器节点。返回结构化文档（见 parse/page.py 落盘格式）。"""
    headings: list[dict] = []
    code_blocks: list[dict] = []
    tables: list[dict] = []
    links: list[dict] = []
    images: list[dict] = []
    path_stack: list[str] = []   # 当前章节路径栈

    def current_path() -> str:
        return " / ".join(path_stack)

    # 按文档序深度优先遍历（仅子树）
    for node in walk_subtree(root):
        if node.tag in _HEADING_TAGS:
            level = _HEADING_TAGS[node.tag]
            text = node.text(strip=True)
            if not text:
                continue
            headings.append({"level": level, "text": text, "path": current_path()})
            # 维护路径栈：弹出同级及以下，压入当前
            while len(path_stack) >= level:
                path_stack.pop()
            path_stack.append(text)

        elif node.tag == "pre":
            code_node = node.css_first("code")
            lang = _code_lang(code_node if code_node is not None else node)
            code = (code_node or node).text()
            if len(code.strip()) < 3:
                continue
            code_blocks.append({
                "index": len(code_blocks),
                "language": lang,
                "code": code.rstrip("\n"),
                "section_path": current_path(),
                "lines": code.count("\n") + 1,
            })

        elif node.tag == "table":
            # 跳过表格内嵌套表格的重复遍历（取最外层）
            if node.parent is not None and node.parent.tag == "table":
                continue
            tables.append(_extract_table(node, current_path()))

        elif node.tag == "a":
            href = node.attributes.get("href", "")
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            abs_url = normalize_url(urljoin(page_url, href))
            if same_site(abs_url, root_url):
                links.append({"target": abs_url, "anchor": node.text(strip=True)[:120],
                              "section_path": current_path()})

        elif node.tag == "img":
            src = node.attributes.get("src", "")
            if src:
                images.append({"src": urljoin(page_url, src),
                               "alt": node.attributes.get("alt", ""),
                               "section_path": current_path()})

    return {"headings": headings, "code_blocks": code_blocks, "tables": tables,
            "links": links, "images": images}


def _code_lang(node: Node) -> str:
    """语言标签：class="language-python" / "highlight-python" / data-lang 等常见约定。"""
    classes = node.attributes.get("class") or ""
    if node.parent is not None:
        classes += " " + (node.parent.attributes.get("class") or "")
    for cls in classes.split():
        for prefix in ("language-", "lang-", "highlight-"):
            if cls.startswith(prefix):
                return cls[len(prefix):].split("-")[0] or ""
    return (node.attributes.get("data-lang") or "").strip()


def _extract_table(table: Node, section_path: str) -> dict:
    """表格 → rows（list[list[str]]）+ markdown。表头取首行 th，无 th 则首行当数据。"""
    rows: list[list[str]] = []
    has_header = False
    for tr in table.css("tr"):
        cells = [c.text(strip=True) for c in tr.css("th, td")]
        if not cells:
            continue
        if tr.css_first("th") is not None and not rows:
            has_header = True
        rows.append(cells)
    md = ""
    if rows:
        width = max(len(r) for r in rows)
        norm = [r + [""] * (width - len(r)) for r in rows]
        if has_header:
            md = "| " + " | ".join(norm[0]) + " |\n"
            md += "|" + "|".join([" --- "] * width) + "|\n"
            md += "\n".join("| " + " | ".join(r) + " |" for r in norm[1:])
        else:
            md = "\n".join("| " + " | ".join(r) + " |" for r in norm)
    return {"index_path": section_path, "rows": rows, "has_header": has_header,
            "markdown": md, "n_rows": len(rows)}


def to_markdown(root: Node) -> str:
    """主内容容器 → Markdown。规则转换（不走 LLM）：标题/代码块/表格/列表/链接/图片。

    traverse 越界问题同上：已作为块级元素输出的子树标记 consumed，避免重复。
    """
    parts: list[str] = []
    consumed: set[int] = set()

    def consume(node: Node) -> None:
        for sub in walk_subtree(node):
            consumed.add(sub.mem_id)

    for node in walk_subtree(root):
        if node.mem_id in consumed:
            continue
        tag = node.tag
        if tag in _HEADING_TAGS:
            level = _HEADING_TAGS[tag]
            parts.append(f"\n{'#' * (level + 1)} {node.text(strip=True)}\n")
            consume(node)
        elif tag == "pre":
            code_node = node.css_first("code")
            lang = _code_lang(code_node if code_node is not None else node)
            parts.append(f"\n```{lang}\n{(code_node or node).text().rstrip()}\n```\n")
            consume(node)
        elif tag == "p":
            parts.append(f"\n{_inline_md(node)}\n")
            consume(node)
        elif tag == "table":
            md = _extract_table(node, "")["markdown"]
            if md:
                parts.append(f"\n{md}\n")
            consume(node)
        elif tag == "li":
            parts.append(f"\n- {_inline_md(node)}")
            consume(node)
        elif tag == "blockquote":
            parts.append(f"\n> {_inline_md(node)}\n")
            consume(node)
    return "".join(parts).strip() + "\n"


def _inline_md(node: Node) -> str:
    """段落内联转换：保留 code/a/strong 的 Markdown 形态。"""
    html = node.html or node.text()
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "lxml")
    for code in soup.find_all("code"):
        code.replace_with(f"`{code.get_text()}`")
    for a in soup.find_all("a"):
        href = a.get("href", "")
        text = a.get_text(strip=True) or href
        a.replace_with(f"[{text}]({href})" if href else text)
    for strong in soup.find_all(["strong", "b"]):
        strong.replace_with(f"**{strong.get_text()}**")
    for em in soup.find_all(["em", "i"]):
        em.replace_with(f"*{em.get_text()}*")
    return soup.get_text().strip()
