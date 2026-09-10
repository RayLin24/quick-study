from __future__ import annotations

import re

SOURCE_LINE = re.compile(
    r"(?:\*source:|#\s*source:)\s*`?([^\s*`]+)(?:#([A-Za-z_][A-Za-z0-9_]*))?`?",
    re.IGNORECASE,
)


def source_symbol_targets(markdown: str) -> list[dict]:
    from utils.source_format import split_source_ref

    found = []
    for match in SOURCE_LINE.finditer(markdown or ""):
        path, line = split_source_ref(match.group(1))
        found.append({"path": path, "symbol": match.group(2), "line": line})
    return found
