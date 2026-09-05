"""Backup / restore the output directory (feature 49)."""

from __future__ import annotations

import shutil
import time
from pathlib import Path


def backup_output(output_dir: Path, dest: Path | None = None) -> dict:
    src = Path(output_dir)
    if not src.is_dir():
        raise FileNotFoundError("output 目录不存在")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(dest) if dest else src.parent / f"output-backup-{stamp}"
    archive = shutil.make_archive(str(dest), "zip", root_dir=str(src))
    return {"archive": archive, "stamp": stamp}


def restore_output(archive: Path, output_dir: Path) -> dict:
    src = Path(archive)
    if not src.is_file():
        raise FileNotFoundError("备份包不存在")
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    shutil.unpack_archive(str(src), str(dest))
    return {"restored": str(dest), "from": str(src)}


def list_backups(parent: Path) -> list[str]:
    return sorted(str(p) for p in Path(parent).glob("output-backup-*.zip"))
