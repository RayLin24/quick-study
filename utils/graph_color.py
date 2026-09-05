"""Color Mermaid dependency graphs by language / directory (feature 13)."""

from __future__ import annotations

import re
from pathlib import Path

LANG_COLORS = {
    "py": ("#DBEAFE", "#1D4ED8"),
    "js": ("#FEF3C7", "#B45309"),
    "ts": ("#DBEAFE", "#1E40AF"),
    "go": ("#CCFBF1", "#0F766E"),
    "rs": ("#FEE2E2", "#B91C1C"),
    "java": ("#FFEDD5", "#C2410C"),
    "md": ("#E2E8F0", "#475569"),
    "default": ("#F1F5F9", "#334155"),
}

DIR_COLORS = {
    "src": ("#DBEAFE", "#1D4ED8"),
    "lib": ("#CCFBF1", "#0F766E"),
    "web": ("#FCE7F3", "#BE185D"),
    "utils": ("#EDE9FE", "#6D28D9"),
    "tests": ("#FEF3C7", "#B45309"),
    "docs": ("#E2E8F0", "#475569"),
}


def lang_of(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in {"py", "pyi", "pyx"}:
        return "py"
    if ext in {"js", "jsx", "mjs"}:
        return "js"
    if ext in {"ts", "tsx"}:
        return "ts"
    if ext in {"go", "rs", "java", "md"}:
        return ext
    return "default"


def dir_of(path: str) -> str:
    posix = (path or "").replace("\\", "/").lstrip("./")
    top = posix.split("/", 1)[0] if posix else ""
    return top if top in DIR_COLORS else "default"


def color_key(path: str, *, by: str = "lang") -> str:
    return dir_of(path) if by == "dir" else lang_of(path)


def palette(key: str, *, by: str = "lang") -> tuple[str, str]:
    table = DIR_COLORS if by == "dir" else LANG_COLORS
    return table.get(key, table.get("default", LANG_COLORS["default"]))


NODE_RE = re.compile(r'^\s*(\w+)\["([^"]+)"\]\s*$')


def color_mermaid(source: str, files: list[str] | None = None, *, by: str = "lang") -> str:
    """Add classDef / class statements. Node labels matching a file path get a color."""
    lines = (source or "").splitlines()
    assignments: dict[str, str] = {}
    file_list = files or []
    for line in lines:
        match = NODE_RE.match(line)
        if not match:
            continue
        node_id, label = match.group(1), match.group(2)
        hit = next((p for p in file_list if Path(p).name in label or p in label or label in p), None)
        if hit:
            assignments[node_id] = color_key(hit, by=by)
        else:
            assignments[node_id] = color_key(label, by=by)
    used = sorted(set(assignments.values()))
    extra = [""]
    for key in used:
        fill, stroke = palette(key, by=by)
        extra.append(f"    classDef {key} fill:{fill},stroke:{stroke},color:#0f172a")
    for node_id, key in assignments.items():
        extra.append(f"    class {node_id} {key}")
    return "\n".join(lines + extra).rstrip() + "\n"
