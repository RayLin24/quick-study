"""Import a zip tutorial pack exported by others (feature 23)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from web.tutorial_ops import SAFE_NAME


class ImportRefused(ValueError):
    """Zip is not a tutorial pack."""


def _safe_rel(name: str) -> Path | None:
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    return rel


def import_tutorial_zip(blob: bytes, output_dir: Path, *, name: str | None = None) -> dict:
    if not blob:
        raise ImportRefused("空文件")
    try:
        zf = zipfile.ZipFile(io.BytesIO(blob))
    except zipfile.BadZipFile as exc:
        raise ImportRefused("不是有效的 zip") from exc
    members = [n for n in zf.namelist() if not n.endswith("/")]
    if not members:
        raise ImportRefused("空压缩包")
    if not any(Path(n).name in {"index.md", "README.md"} for n in members):
        raise ImportRefused("压缩包里没有 index.md / README.md，不是教程包")
    dest_name = (name or "").strip()
    if not dest_name:
        tops = {Path(n).parts[0] for n in members if Path(n).parts}
        dest_name = next(iter(tops)) if len(tops) == 1 else "imported"
    if not SAFE_NAME.match(dest_name):
        dest_name = "imported"
    dest = Path(output_dir) / dest_name
    dest.mkdir(parents=True, exist_ok=True)
    strip_root = all(Path(n).parts and Path(n).parts[0] == dest_name for n in members)
    for raw in members:
        rel = _safe_rel(raw)
        if rel is None:
            raise ImportRefused("zip 含非法路径")
        if strip_root and rel.parts:
            rel = Path(*rel.parts[1:]) if len(rel.parts) > 1 else Path(rel.name)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(raw))
    if not (dest / "index.md").is_file() and (dest / "README.md").is_file():
        (dest / "index.md").write_text((dest / "README.md").read_text(encoding="utf-8"), encoding="utf-8")
    if not (dest / "index.md").is_file() and not (dest / "README.md").is_file():
        raise ImportRefused("解压后仍没有教程入口")
    return {"name": dest_name, "files": sorted(p.name for p in dest.iterdir() if p.is_file())}
