"""Local-repo watch: hint incremental regen when files change (feature 16)."""

from __future__ import annotations

import json
import os
from pathlib import Path


def _fingerprint(path: Path) -> dict:
    try:
        st = path.stat()
    except OSError:
        return {"path": str(path), "missing": True}
    return {"path": str(path), "mtime": int(st.st_mtime), "size": int(st.st_size)}


def snapshot_local_dir(local_dir: str | Path, *, limit: int = 400) -> list[dict]:
    root = Path(local_dir)
    if not root.is_dir():
        return []
    items = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/.git/" in rel:
            continue
        items.append({"rel": rel, **_fingerprint(path)})
        if len(items) >= limit:
            break
    return items


def save_watch_snapshot(folder: Path, files: list[dict]) -> None:
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "watch.json").write_text(
        json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8"
    )


def load_watch_snapshot(folder: Path) -> list[dict]:
    path = Path(folder) / "watch.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return data.get("files") if isinstance(data, dict) else []


def watch_status(folder: Path, local_dir: str | Path | None = None) -> dict:
    meta_path = Path(folder) / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    target = local_dir or meta.get("local_dir")
    if not target or not Path(target).is_dir():
        return {"ok": False, "reason": "no_local_dir", "changed": []}
    previous = {item.get("rel"): item for item in load_watch_snapshot(folder)}
    current = snapshot_local_dir(target)
    changed = []
    for item in current:
        old = previous.get(item["rel"])
        if old is None or old.get("mtime") != item.get("mtime") or old.get("size") != item.get("size"):
            changed.append(item["rel"])
    return {
        "ok": True,
        "local_dir": str(target),
        "changed": changed[:50],
        "changed_count": len(changed),
        "needs_incremental": bool(changed),
        "file_count": len(current),
    }
