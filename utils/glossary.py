from __future__ import annotations

import re
from pathlib import Path

from utils.ask_tutorial import HEADING_RE

BOLD_TERM = re.compile(r"\*\*([^*]{2,40})\*\*")


def build_glossary(folder: Path) -> str:
    root = Path(folder)
    terms: dict[str, str] = {}
    for path in sorted(root.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        heading = HEADING_RE.search(text)
        chapter = heading.group(1).strip() if heading else path.stem
        for term in BOLD_TERM.findall(text):
            key = term.strip()
            if key and key not in terms:
                terms[key] = chapter
    lines = ["# 术语表", "", "从各章加粗词收集。", ""]
    for term in sorted(terms, key=str.lower):
        lines.append(f"- **{term}** — 首次见于 {terms[term]}")
    return "\n".join(lines) + "\n"
