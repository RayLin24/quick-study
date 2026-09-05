"""Isolate cancelled / half-written tutorials so they do not appear in the library."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

PARTIAL_DIRNAME = ".partial"


def expected_output_name(payload: dict) -> str | None:
    from utils.preview import derive_project_name

    name = derive_project_name(payload or {})
    return name or None


def isolate_cancelled_output(
    output_dir: Path,
    name: str | None,
    *,
    job_id: str = "",
) -> Path | None:
    """Move a cancelled tutorial folder under output/.partial/.

    Returns the destination path, or None if there was nothing to move.
    """
    if not name or name in {PARTIAL_DIRNAME, ".", ".."}:
        return None
    if Path(name).name != name:
        return None
    root = Path(output_dir)
    src = (root / name).resolve()
    try:
        src.relative_to(root.resolve())
    except ValueError:
        return None
    if not src.is_dir():
        return None
    dest_root = root / PARTIAL_DIRNAME
    dest_root.mkdir(parents=True, exist_ok=True)
    stamp = job_id or str(int(time.time()))
    dest = dest_root / f"{name}-{stamp}"
    if dest.exists():
        dest = dest_root / f"{name}-{stamp}-{int(time.time() * 1000)}"
    shutil.move(str(src), str(dest))
    return dest


def is_library_entry(path: Path) -> bool:
    """True if this folder should appear on the home list."""
    if not path.is_dir():
        return False
    if path.name.startswith("."):
        return False
    if path.name == PARTIAL_DIRNAME:
        return False
    return True
