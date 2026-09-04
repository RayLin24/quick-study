from __future__ import annotations

import io
import zipfile
from pathlib import Path


def extract_source_zip(blob: bytes, dest: Path, *, max_files: int = 4000) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        names = [info for info in zf.infolist() if not info.is_dir()]
        if len(names) > max_files:
            raise ValueError(f"zip 内文件过多（{len(names)}）")
        for info in names:
            name = info.filename.replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"非法 zip 路径: {info.filename}")
            target = dest / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
    children = [p for p in dest.iterdir() if p.is_dir()]
    files = [p for p in dest.iterdir() if p.is_file()]
    if len(children) == 1 and not files:
        return children[0]
    return dest
