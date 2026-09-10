"""Robust diagram node → chapter routing (#32). Prefer data-id / click row."""

from __future__ import annotations

import re
from pathlib import Path

CLICK_RE = re.compile(r'^\s*click\s+(A\d+)\s+"([^"]+)"\s*$', re.M)
NODE_ID_RE = re.compile(r"(A\d+)")


def parse_mermaid_clicks(index_text: str) -> dict[str, str]:
    """Map mermaid node id (A0) → chapter filename from `click A0 "01_x.md"`."""
    out: dict[str, str] = {}
    for match in CLICK_RE.finditer(index_text or ""):
        out[match.group(1)] = Path(match.group(2).split("#", 1)[0]).name
    return out


def attach_node_ids(chapters: list[dict], index_text: str = "") -> list[dict]:
    clicks = parse_mermaid_clicks(index_text)
    by_file = {Path(fn).name: nid for nid, fn in clicks.items()}
    content = [ch for ch in chapters if ch.get("filename") not in {None, "index.md", "README.md"}]
    attached = []
    seq = 0
    for item in chapters:
        row = dict(item)
        filename = row.get("filename") or ""
        if filename in {"index.md", "README.md"}:
            row["node_id"] = None
        elif filename in by_file:
            row["node_id"] = by_file[filename]
        else:
            row["node_id"] = f"A{seq}"
            seq += 1
        attached.append(row)
    if not clicks:
        seq = 0
        for row in attached:
            if row.get("filename") not in {None, "index.md", "README.md"}:
                row["node_id"] = f"A{seq}"
                seq += 1
    return attached


def extract_node_id(*candidates: str) -> str | None:
    for raw in candidates:
        text = str(raw or "")
        if not text:
            continue
        # Prefer isolated A<digits> (data-id / flowchart-A0-123 / A0)
        match = re.search(r"(?:^|[\s#_.-])(A\d+)(?:$|[\s#_.-])", text)
        if match:
            return match.group(1)
        match = NODE_ID_RE.search(text)
        if match and text.strip() == match.group(1):
            return match.group(1)
    return None


def resolve_diagram_href(
    *,
    data_id: str = "",
    element_id: str = "",
    click_file: str = "",
    text_content: str = "",
    chapters: list[dict],
) -> str | None:
    """Match node → chapter href. data-id / click filename beat fragile textContent."""
    by_node = {ch.get("node_id"): ch for ch in chapters if ch.get("node_id")}
    by_file = {ch.get("filename"): ch for ch in chapters if ch.get("filename")}
    node_id = extract_node_id(data_id, element_id)
    if node_id and node_id in by_node:
        return by_node[node_id].get("href")
    fname = Path(click_file).name if click_file else ""
    if fname in by_file:
        return by_file[fname].get("href")
    label = (text_content or "").strip()
    if not label:
        return None
    # Exact title only — do not substring-match mixed SVG textContent.
    for ch in chapters:
        if ch.get("filename") in {None, "index.md"}:
            continue
        if (ch.get("title") or "").strip() == label:
            return ch.get("href")
    return None
