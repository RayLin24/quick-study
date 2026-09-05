from __future__ import annotations

import re

SOURCE_LINE = re.compile(
    r"(?:\*source:|#\s*source:)\s*`?([^\s*`]+)(?:#([A-Za-z_][A-Za-z0-9_]*))?`?",
    re.IGNORECASE,
)


def source_symbol_targets(markdown: str) -> list[dict]:
    found = []
    for match in SOURCE_LINE.finditer(markdown or ""):
        found.append({"path": match.group(1), "symbol": match.group(2)})
    return found
