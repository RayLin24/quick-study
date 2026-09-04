from __future__ import annotations

import re
import shutil
from pathlib import Path

from web.render import resolve_tutorial_file, tutorial_index_path

SAFE_NAME = re.compile(r"^[A-Za-z0-9._\u4e00-\u9fff][A-Za-z0-9._\u4e00-\u9fff -]{0,80}$")


def tutorial_folder(output_dir: Path, name: str) -> Path:
    if Path(name).name != name or not name or name.startswith("."):
        raise ValueError("非法路径")
    root = Path(output_dir).resolve()
    folder = (root / name).resolve()
    try:
        folder.relative_to(root)
    except ValueError as exc:
        raise ValueError("非法路径") from exc
    if not folder.is_dir() or tutorial_index_path(folder) is None:
        raise FileNotFoundError(name)
    return folder


def delete_tutorial(output_dir: Path, name: str) -> None:
    folder = tutorial_folder(output_dir, name)
    shutil.rmtree(folder)


def rename_tutorial(output_dir: Path, name: str, new_name: str) -> str:
    dest_name = (new_name or "").strip()
    if not SAFE_NAME.match(dest_name) or Path(dest_name).name != dest_name:
        raise ValueError("新名称不合法")
    src = tutorial_folder(output_dir, name)
    dest = (Path(output_dir).resolve() / dest_name).resolve()
    dest.relative_to(Path(output_dir).resolve())
    if dest.exists():
        raise ValueError("目标名称已存在")
    src.rename(dest)
    return dest_name


def clear_tutorial_cache(folder: Path) -> int:
    cache = Path(folder) / ".llm_cache"
    if not cache.is_dir():
        return 0
    count = len(list(cache.glob("*.json")))
    shutil.rmtree(cache)
    return count
