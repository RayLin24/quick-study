"""One-click 'open in editor' URLs (feature 15)."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote


def vscode_file_url(path: str, *, line: int | None = None) -> str:
    raw = Path(path).expanduser().as_posix()
    if not raw.startswith("/"):
        raw = "/" + raw.lstrip("/")
    url = f"vscode://file{quote(raw, safe='/:')}"
    if line and int(line) > 0:
        url += f":{int(line)}"
    return url


def resolve_editor_url(rel_path: str, *, local_dir: str | None = None, line: int | None = None) -> dict:
    rel = (rel_path or "").split("#", 1)[0].strip()
    abs_path = str(Path(local_dir, rel).resolve()) if local_dir else rel
    return {
        "path": rel,
        "absolute": abs_path,
        "vscode": vscode_file_url(abs_path, line=line),
    }
