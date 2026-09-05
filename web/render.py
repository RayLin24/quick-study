from __future__ import annotations

import hashlib
import html as html_lib
import json
import re
from datetime import datetime
from pathlib import Path

import markdown

from utils.partial import is_library_entry

TRUNCATION_MARKER_RE = re.compile(r"<!--\s*qs:truncated_files=(\d+)\s+total=(\d+)\s*-->")
MERMAID_CLICK_RE = re.compile(r'^\s*click\s+(\S+)\s+"([^"]+)"\s*$', re.M)

FRONT_MATTER = re.compile(r"^---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
MERMAID_FENCE = re.compile(r"```mermaid\s*\n(.*?)```", re.DOTALL)
CODE_FENCE = re.compile(r"```[\w+-]*[^\n]*\n.*?```", re.DOTALL)
HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
HREF_RE = re.compile(r'href="([^"]+)"')
HEADING_RE = re.compile(r"^#\s+(.+)$", re.M)
TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)
H1_RE = re.compile(r"<h1>(.*?)</h1>", re.DOTALL)
H2_HTML_RE = re.compile(r"<h2(\s[^>]*)?>(.*?)</h2>", re.DOTALL | re.IGNORECASE)
IMG_RE = re.compile(r"<img\b[^>]*>", re.I)
INDEX_FILENAMES = ("index.md", "README.md")

COVER_PALETTES = (
    ("#FFF8EF", "#DBEAFE", "#CCFBF1", "#FEF3C7", "#2563EB", "#0D9488", "#D97706", "#1E3A5F"),
    ("#F0FDFA", "#CCFBF1", "#DBEAFE", "#FCE7F3", "#0D9488", "#2563EB", "#DB2777", "#115E59"),
    ("#EFF6FF", "#DBEAFE", "#FEF3C7", "#E2E8F0", "#1D4ED8", "#D97706", "#475569", "#1E3A8A"),
    ("#FFFBEB", "#FEF3C7", "#DBEAFE", "#CCFBF1", "#B45309", "#2563EB", "#0F766E", "#78350F"),
)


def tutorial_index_path(folder: Path) -> Path | None:
    for name in INDEX_FILENAMES:
        path = folder / name
        if path.is_file():
            return path
    return None


def _read_meta(folder: Path) -> dict:
    path = folder / "meta.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def list_tutorials(output_dir: Path) -> list[dict]:
    if not output_dir.is_dir():
        return []
    items = []
    for child in sorted(output_dir.iterdir(), key=lambda p: p.name.lower()):
        if not is_library_entry(child):
            continue
        index = tutorial_index_path(child) if child.is_dir() else None
        if index is None:
            continue
        mtime = index.stat().st_mtime
        meta = _read_meta(child)
        language = meta.get("language")
        chapters = len([p for p in child.glob("*.md") if p.name not in {"index.md", "README.md", "glossary.md", "heatmap.md"}])
        items.append(
            {
                "name": child.name,
                "language": language,
                "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M"),
                "mtime_epoch": mtime,
                "repo_url": meta.get("repo_url"),
                "usage": meta.get("usage"),
                "file_count": meta.get("file_count"),
                "chapter_count": meta.get("chapter_count") or chapters,
                "strategy": meta.get("strategy"),
            }
        )
    items.sort(key=lambda item: item.get("mtime_epoch") or 0, reverse=True)
    return items


def resolve_tutorial_file(output_root: Path, tutorial: str, filename: str) -> Path:
    if Path(tutorial).name != tutorial or Path(filename).name != filename:
        raise ValueError("非法路径")
    if not filename.endswith(".md"):
        raise ValueError("只支持 Markdown 文件")
    root = output_root.resolve()
    target = (root / tutorial / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError("非法路径") from exc
    if target.is_file():
        return target
    if filename == "index.md":
        readme = (root / tutorial / "README.md").resolve()
        try:
            readme.relative_to(root)
        except ValueError as exc:
            raise ValueError("非法路径") from exc
        if readme.is_file():
            return readme
    raise FileNotFoundError(str(target))


def parse_truncation_note(text: str) -> dict | None:
    match = TRUNCATION_MARKER_RE.search(text or "")
    if not match:
        return None
    return {"truncated": int(match.group(1)), "total": int(match.group(2))}


def mermaid_click_bindings(chapters: list[dict]) -> dict[str, str]:
    return {
        item["title"]: item["href"]
        for item in chapters
        if item.get("filename") not in {None, "index.md"} and item.get("title") and item.get("href")
    }


def first_heading(text: str) -> str | None:
    match = HEADING_RE.search(text)
    return match.group(1).strip() if match else None


def slugify_heading(text: str) -> str:
    plain = re.sub(r"<[^>]+>", "", text)
    plain = html_lib.unescape(plain).strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", plain, flags=re.UNICODE).strip("-").lower()
    if slug and re.fullmatch(r"[\u4e00-\u9fff-]+", slug):
        digest = hashlib.md5(plain.encode("utf-8")).hexdigest()[:4]
        slug = f"{slug}-{digest}"
    return slug or "section"


def add_h2_ids(html: str) -> tuple[str, list[dict]]:
    toc: list[dict] = []
    seen: dict[str, int] = {}

    def repl(match: re.Match[str]) -> str:
        attrs = match.group(1) or ""
        inner = match.group(2)
        existing = re.search(r'\bid="([^"]+)"', attrs)
        slug = existing.group(1) if existing else slugify_heading(inner)
        count = seen.get(slug, 0) + 1
        seen[slug] = count
        if count > 1:
            slug = f"{slug}-{count}"
        plain = html_lib.unescape(re.sub(r"<[^>]+>", "", inner)).strip()
        toc.append({"id": slug, "text": plain})
        if existing:
            return match.group(0)
        extra = attrs.rstrip()
        return f"<h2{extra} id=\"{html_lib.escape(slug, quote=True)}\">{inner}</h2>"

    return H2_HTML_RE.sub(repl, html), toc


def chapter_label(filename: str, heading: str | None) -> str:
    if heading:
        return re.sub(r"^Chapter\s+\d+\s*:\s*", "", heading, flags=re.I).strip()
    stem = Path(filename).stem
    return re.sub(r"^\d+_", "", stem).replace("_", " ").strip() or stem


def build_cover_svg(title: str) -> str:
    """LangChain-docs style concept board: warm paper + labeled blocks + arrows."""
    palette = COVER_PALETTES[sum(ord(ch) for ch in title) % len(COVER_PALETTES)]
    bg, a, b, c, sa, sb, sc, ink = palette
    label = html_lib.escape(title)
    short = title.strip()
    if len(short) > 26:
        short = short[:25] + "…"
    short = html_lib.escape(short)
    boxes = (
        (48, 108, 200, 92, a, sa, "输入", "消息 / 请求"),
        (368, 96, 224, 108, b, sb, "核心抽象", short),
        (712, 108, 200, 92, c, sc, "输出", "结果 / 下一章"),
    )
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 960 280" role="img" aria-label="{label}">',
        f'<rect width="960" height="280" fill="{bg}"/>',
        f'<text x="48" y="48" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" font-size="22" font-weight="700" fill="{ink}">概念一览</text>',
        f'<text x="48" y="74" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" font-size="13" fill="#5B6B7A">先看结构，再读代码</text>',
    ]
    for x, y, w, h, fill, stroke, top, bottom in boxes:
        parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        )
        cx = x + w / 2
        parts.append(
            f'<text x="{cx}" y="{y + 38}" text-anchor="middle" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" font-size="15" font-weight="700" fill="{ink}">{html_lib.escape(top)}</text>'
        )
        parts.append(
            f'<text x="{cx}" y="{y + 64}" text-anchor="middle" font-family="Segoe UI, PingFang SC, Microsoft YaHei, sans-serif" font-size="12" fill="#334155">{html_lib.escape(bottom)}</text>'
        )
    parts.append(f'<polygon points="268,154 336,154 336,146 368,162 336,178 336,170 268,170" fill="{sb}"/>')
    parts.append(f'<polygon points="612,154 680,154 680,146 712,162 680,178 680,170 612,170" fill="{sc}"/>')
    parts.append("</svg>")
    return "".join(parts)


def _escape_raw_html_outside_code(text: str) -> str:
    """Keep fenced code intact; show raw HTML tags as text so they cannot leak."""
    code_blocks: list[str] = []

    def stash_code(match: re.Match[str]) -> str:
        code_blocks.append(match.group(0))
        return f"@@CODE{len(code_blocks) - 1}@@"

    prepared = CODE_FENCE.sub(stash_code, text)
    prepared = HTML_TAG_RE.sub(lambda match: html_lib.escape(match.group(0)), prepared)
    for index, block in enumerate(code_blocks):
        prepared = prepared.replace(f"@@CODE{index}@@", block)
    return prepared


def markdown_to_html(
    text: str,
    tutorial_name: str,
    *,
    cover: bool = False,
    repo_url: str | None = None,
    local_dir: str | None = None,
    return_toc: bool = False,
):
    if text.startswith("---"):
        text = FRONT_MATTER.sub("", text, count=1)
    placeholders: list[str] = []

    def stash(match: re.Match[str]) -> str:
        placeholders.append(match.group(1).strip())
        return f"@@MERMAID{len(placeholders) - 1}@@"

    prepared = _escape_raw_html_outside_code(MERMAID_FENCE.sub(stash, text))
    body = markdown.markdown(prepared, extensions=["fenced_code", "tables", "sane_lists"])
    heading = first_heading(text) or tutorial_name
    for index, source in enumerate(placeholders):
        caption = "结构图" if index == 0 else f"结构图 {index + 1}"
        extra = " diagram--hero" if index == 0 else ""
        replacement = (
            f'<figure class="diagram{extra}">'
            f"<figcaption>{caption}</figcaption>"
            f'<div class="mermaid-zoom" data-zoom="1">'
            f'<div class="mermaid-toolbar">'
            f'<button type="button" class="mermaid-zoom-in" aria-label="放大">+</button>'
            f'<button type="button" class="mermaid-zoom-out" aria-label="缩小">−</button>'
            f'<button type="button" class="mermaid-zoom-reset" aria-label="重置">1:1</button>'
            f"</div>"
            f'<div class="mermaid-scroller">'
            f'<div class="mermaid">{html_lib.escape(source)}</div>'
            f"</div></div>"
            f"</figure>"
        )
        body = body.replace(f"<p>@@MERMAID{index}@@</p>", replacement)
        body = body.replace(f"@@MERMAID{index}@@", replacement)

    def rewrite_href(match: re.Match[str]) -> str:
        href = match.group(1)
        if href.startswith(("http://", "https://", "/", "#", "mailto:")):
            return match.group(0)
        if href.endswith(".md"):
            return f'href="/t/{tutorial_name}/{href}"'
        return match.group(0)

    body = HREF_RE.sub(rewrite_href, body)
    body = TABLE_RE.sub(lambda match: f'<div class="table-wrap">{match.group(0)}</div>', body)
    body = IMG_RE.sub(_promote_image, body)
    body = re.sub(r"(</h1>\s*)<p>", r'\1<p class="lead">', body, count=1)
    if cover:
        cover_html = f'<figure class="cover">{build_cover_svg(chapter_label("index.md", heading))}</figure>'
        body = H1_RE.sub(lambda match: f"{match.group(0)}\n{cover_html}", body, count=1)
    body, toc = add_h2_ids(body)
    from utils.source_format import linkify_source_html

    body = linkify_source_html(body, repo_url=repo_url, local_dir=local_dir)
    body = _attach_editor_buttons(body)
    if return_toc:
        return body, toc
    return body


def _attach_editor_buttons(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        path = match.group(2)
        return (
            f"{match.group(1)}"
            f'<a class="open-editor" href="vscode://file/{html_lib.escape(path, quote=True)}" '
            f'data-path="{html_lib.escape(path, quote=True)}">在编辑器打开</a>'
        )

    return re.sub(
        r'(<code class="source-path" data-path="([^"]+)">.*?</code>)',
        repl,
        html or "",
        flags=re.S,
    )


def _promote_image(match: re.Match[str]) -> str:
    tag = match.group(0)
    if "class=" in tag:
        return tag
    return tag[:-1] + ' class="shot">'
