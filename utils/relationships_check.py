from __future__ import annotations

import re

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


def relationship_coverage(num_abstractions: int, details: list) -> dict:
    seen: set[int] = set()
    for rel in details or []:
        if isinstance(rel.get("from"), int):
            seen.add(rel["from"])
        if isinstance(rel.get("to"), int):
            seen.add(rel["to"])
    orphans = [i for i in range(num_abstractions) if i not in seen]
    return {
        "orphans": orphans,
        "edge_count": len(details or []),
        "ok": not orphans and bool(details),
    }


def missing_relationship_links(chapter_text: str, relationship_edges: str) -> list[str]:
    """Filenames mentioned in the edge list that never appear as Markdown links."""
    wanted = {match.group(2) for match in LINK_RE.finditer(relationship_edges or "")}
    have = {match.group(2) for match in LINK_RE.finditer(chapter_text or "")}
    return sorted(wanted - have)


def append_required_links(chapter_text: str, relationship_edges: str) -> str:
    missing = missing_relationship_links(chapter_text, relationship_edges)
    if not missing:
        return chapter_text
    lines = [chapter_text.rstrip(), "", "## 相关章节", ""]
    for href in missing:
        label = href
        for match in LINK_RE.finditer(relationship_edges or ""):
            if match.group(2) == href:
                label = match.group(1)
                break
        lines.append(f"- [{label}]({href})")
    return "\n".join(lines) + "\n"
