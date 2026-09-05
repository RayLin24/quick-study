"""Symbol-centered slices for map_mode chapter writing."""

from __future__ import annotations

from utils.repo_map import extract_symbols
from utils.source_format import unified_source_line


def slice_file(path: str, content: str, *, max_chars: int = 1800) -> str:
    text = content or ""
    symbols = extract_symbols(path, text)
    header = f"--- File: {path} ---\n{unified_source_line(path)}\n"
    if symbols:
        header += f"symbols: {', '.join(symbols)}\n"
    if len(text) <= max_chars:
        return header + text
    # Prefer a window around the first symbol occurrence.
    pivot = 0
    for name in symbols:
        idx = text.find(name)
        if idx >= 0:
            pivot = max(0, idx - max_chars // 4)
            break
    chunk = text[pivot : pivot + max_chars]
    return header + chunk + "\n... [slice]\n"


def clip_map_mode_snippets(content_map, *, max_total_chars: int = 24000, max_file_chars: int = 1800) -> str:
    parts = []
    used = 0
    for idx_path, content in (content_map or {}).items():
        label = idx_path.split("# ", 1)[1] if "# " in idx_path else idx_path
        piece = slice_file(label, content or "", max_chars=max_file_chars) + "\n"
        if used + len(piece) > max_total_chars:
            break
        parts.append(piece)
        used += len(piece)
    return "".join(parts).rstrip()
