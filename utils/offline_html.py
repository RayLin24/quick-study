from __future__ import annotations

import html
from pathlib import Path

import json

from utils.ask_tutorial import HEADING_RE
from web.render import markdown_to_html


def build_offline_html(folder: Path) -> str:
    root = Path(folder)
    parts = [
        "<!DOCTYPE html><html lang='zh-CN'><head><meta charset='utf-8'>",
        f"<title>{html.escape(root.name)} · Quick Study 离线</title>",
        "<style>body{font-family:Georgia,serif;max-width:860px;margin:2rem auto;padding:0 1rem;line-height:1.6}"
        "nav a{margin-right:1rem}pre{overflow:auto;background:#f6f8fa;padding:1rem}"
        "h1,h2{border-bottom:1px solid #eee}</style></head><body>",
        f"<h1>{html.escape(root.name)}（单文件离线）</h1><nav>",
    ]
    meta = {}
    meta_path = root / "meta.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    chapters = sorted(root.glob("*.md"))
    for path in chapters:
        text = path.read_text(encoding="utf-8")
        match = HEADING_RE.search(text)
        title = match.group(1).strip() if match else path.stem
        parts.append(f"<a href='#{html.escape(path.stem)}'>{html.escape(title)}</a>")
    parts.append("</nav>")
    for path in chapters:
        text = path.read_text(encoding="utf-8")
        body, _toc = markdown_to_html(
            text,
            root.name,
            cover=path.name == "index.md",
            repo_url=meta.get("repo_url"),
            sha=meta.get("upstream_commit"),
            return_toc=True,
        )
        parts.append(f"<section id='{html.escape(path.stem)}'>")
        parts.append(body)
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)
