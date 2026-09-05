from __future__ import annotations

from pathlib import Path


def gitingest_digest(files: list[tuple[str, str]], *, max_chars: int = 20000) -> str:
    lines = ["# Digest", ""]
    used = 0
    for path, content in files:
        header = f"## {path}\n"
        body = content or ""
        remain = max_chars - used - len(header)
        if remain <= 0:
            lines.append("…truncated…")
            break
        chunk = body[:remain]
        lines.append(header + chunk)
        used += len(header) + len(chunk)
    return "\n".join(lines).rstrip() + "\n"
