from __future__ import annotations

import difflib
import shutil
import time
from pathlib import Path


def versions_dir(folder: Path) -> Path:
    return Path(folder) / ".versions"


def snapshot_tutorial(folder: Path, *, label: str | None = None) -> str:
    root = Path(folder)
    stamp = label or time.strftime("%Y%m%d-%H%M%S")
    dest = versions_dir(root) / stamp
    dest.mkdir(parents=True, exist_ok=True)
    for path in root.glob("*.md"):
        shutil.copy2(path, dest / path.name)
    meta = root / "meta.json"
    if meta.is_file():
        shutil.copy2(meta, dest / "meta.json")
    return stamp


def list_versions(folder: Path) -> list[str]:
    root = versions_dir(folder)
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def read_version_file(folder: Path, version: str, filename: str = "index.md") -> str:
    path = versions_dir(folder) / version / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path.read_text(encoding="utf-8")


def diff_versions(folder: Path, older: str, newer: str, filename: str = "index.md") -> str:
    a = read_version_file(folder, older, filename).splitlines(keepends=True)
    b = read_version_file(folder, newer, filename).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(a, b, fromfile=f"{older}/{filename}", tofile=f"{newer}/{filename}")
    )
