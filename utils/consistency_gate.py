"""Light chapter narrative consistency: glossary align + dead/required links.

Strong polish (--polish) stays optional and is not invoked here.
"""

from __future__ import annotations

import re
from pathlib import Path

from utils.ask_tutorial import HEADING_RE
from utils.dead_links import find_dead_markdown_links
from utils.glossary import BOLD_TERM
from utils.relationships_check import LINK_RE, append_required_links

SKIP_MD = {"glossary.md", "heatmap.md"}
INDEX_NAMES = {"index.md", "README.md"}


def _chapter_files(folder: Path) -> list[Path]:
    root = Path(folder)
    return [
        path
        for path in sorted(root.glob("*.md"))
        if path.name not in SKIP_MD
    ]


def collect_canonical_terms(folder: Path) -> dict[str, str]:
    """First-seen bold term wins as the canonical spelling."""
    canonical: dict[str, str] = {}
    for path in _chapter_files(folder):
        text = path.read_text(encoding="utf-8")
        for term in BOLD_TERM.findall(text):
            key = term.strip()
            if len(key) < 2:
                continue
            folded = key.casefold()
            if folded not in canonical:
                canonical[folded] = key
    return canonical


def align_glossary_text(text: str, canonical: dict[str, str]) -> tuple[str, int]:
    if not canonical:
        return text, 0
    changed = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        term = match.group(1)
        canon = canonical.get(term.casefold())
        if canon and canon != term:
            changed += 1
            return f"**{canon}**"
        return match.group(0)

    return BOLD_TERM.sub(repl, text), changed


def required_index_links(folder: Path) -> str:
    lines = []
    for path in _chapter_files(folder):
        if path.name in INDEX_NAMES:
            continue
        text = path.read_text(encoding="utf-8")
        heading = HEADING_RE.search(text)
        title = heading.group(1).strip() if heading else path.stem
        lines.append(f"- [{title}]({path.name})")
    return "\n".join(lines)


def chapters_missing_required_links(folder: Path) -> list[str]:
    """Chapters that never link to index or another chapter."""
    missing = []
    names = {path.name for path in _chapter_files(folder)}
    for path in _chapter_files(folder):
        if path.name in INDEX_NAMES:
            continue
        text = path.read_text(encoding="utf-8")
        targets = {
            Path(match.group(2).split("#", 1)[0]).name
            for match in LINK_RE.finditer(text)
        }
        if not (targets & names):
            missing.append(path.name)
    return missing


def apply_light_fixes(folder: Path) -> dict:
    root = Path(folder)
    canonical = collect_canonical_terms(root)
    aligned = 0
    for path in _chapter_files(root):
        original = path.read_text(encoding="utf-8")
        updated, n = align_glossary_text(original, canonical)
        aligned += n
        if n:
            path.write_text(updated, encoding="utf-8")

    required = required_index_links(root)
    linked = 0
    for name in chapters_missing_required_links(root):
        path = root / name
        before = path.read_text(encoding="utf-8")
        after = append_required_links(before, required)
        if after != before:
            path.write_text(after, encoding="utf-8")
            linked += 1

    dead = find_dead_markdown_links(root)
    still_missing = chapters_missing_required_links(root)
    return {
        "ok": not dead and not still_missing,
        "glossary_aligned": aligned,
        "required_links_added": linked,
        "dead_links": dead,
        "missing_required_links": still_missing,
        "canonical_terms": len(canonical),
        "polish": False,
    }


def run_consistency_gate(folder: Path) -> dict:
    return apply_light_fixes(folder)
