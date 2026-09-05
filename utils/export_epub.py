"""Minimal EPUB export for long-form reading (feature 25). No extra deps."""

from __future__ import annotations

import html
import io
import zipfile
from pathlib import Path

from utils.ask_tutorial import HEADING_RE
from web.render import markdown_to_html


CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def _chapter_xhtml(title: str, body_html: str) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml">'
        f"<head><title>{html.escape(title)}</title></head>"
        f"<body>{body_html}</body></html>"
    )


def build_epub(folder: Path) -> bytes:
    root = Path(folder)
    chapters = []
    for path in sorted(root.glob("*.md")):
        if path.name in {"glossary.md", "heatmap.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        heading = HEADING_RE.search(text)
        title = heading.group(1).strip() if heading else path.stem
        body = markdown_to_html(text, root.name, cover=False)
        chapters.append((path.stem, title, body))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        manifest = []
        spine = []
        nav_li = []
        for idx, (stem, title, body) in enumerate(chapters):
            href = f"{idx:02d}_{stem}.xhtml"
            zf.writestr(f"OEBPS/{href}", _chapter_xhtml(title, body))
            manifest.append(f'<item id="c{idx}" href="{href}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{idx}"/>')
            nav_li.append(f'<li><a href="{href}">{html.escape(title)}</a></li>')
        zf.writestr(
            "OEBPS/nav.xhtml",
            '<?xml version="1.0" encoding="utf-8"?>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">'
            f"<head><title>nav</title></head><body><nav epub:type=\"toc\"><ol>{''.join(nav_li)}</ol></nav></body></html>",
        )
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        opf = (
            '<?xml version="1.0" encoding="utf-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="bid" version="3.0">'
            "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
            f"<dc:identifier id=\"bid\">quick-study-{html.escape(root.name)}</dc:identifier>"
            f"<dc:title>{html.escape(root.name)}</dc:title>"
            "<dc:language>zh</dc:language>"
            "</metadata>"
            f"<manifest>{''.join(manifest)}</manifest>"
            f"<spine>{''.join(spine)}</spine>"
            "</package>"
        )
        zf.writestr("OEBPS/content.opf", opf)
    return buf.getvalue()
