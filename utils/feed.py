"""RSS / Atom feed of local tutorial updates (feature 26)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from web.render import list_tutorials


def atom_feed(output_dir: Path, *, base_url: str = "http://127.0.0.1:8000") -> str:
    items = list_tutorials(output_dir)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = []
    for item in items:
        href = f"{base_url.rstrip('/')}/t/{item['name']}"
        stamp = item.get("mtime") or updated
        entries.append(
            "<entry>"
            f"<title>{escape(item['name'])}</title>"
            f"<id>{escape(href)}</id>"
            f"<updated>{escape(str(stamp).replace(' ', 'T'))}Z</updated>"
            f"<link href=\"{escape(href)}\"/>"
            f"<summary>{escape(str(item.get('language') or ''))} · {escape(str(item.get('chapter_count') or ''))} 章</summary>"
            "</entry>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<title>Quick Study 教程更新</title>"
        f"<updated>{updated}</updated>"
        f"<id>{escape(base_url)}/feed.xml</id>"
        + "".join(entries)
        + "</feed>"
    )


def rss_feed(output_dir: Path, *, base_url: str = "http://127.0.0.1:8000") -> str:
    items = list_tutorials(output_dir)
    parts = [
        '<?xml version="1.0" encoding="utf-8"?>',
        '<rss version="2.0"><channel>',
        "<title>Quick Study 教程更新</title>",
        f"<link>{escape(base_url)}</link>",
        "<description>本地教程库更新</description>",
    ]
    for item in items:
        href = f"{base_url.rstrip('/')}/t/{item['name']}"
        parts.append(
            f"<item><title>{escape(item['name'])}</title><link>{escape(href)}</link>"
            f"<guid>{escape(href)}</guid></item>"
        )
    parts.append("</channel></rss>")
    return "".join(parts)
