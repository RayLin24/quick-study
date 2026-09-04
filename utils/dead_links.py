from __future__ import annotations

import re
from pathlib import Path

MD_LINK_RE = re.compile(r"\]\(([^)]+\.md)(?:#[^)]*)?\)")


def find_dead_markdown_links(folder: Path) -> list[dict]:
    root = Path(folder)
    names = {path.name for path in root.glob("*.md")}
    dead = []
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        for href in MD_LINK_RE.findall(text):
            target = Path(href.split("#", 1)[0]).name
            if target and target not in names:
                dead.append({"from": path.name, "href": href, "target": target})
    return dead
