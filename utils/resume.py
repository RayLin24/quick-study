"""Checkpoint completed chapters so a failed run can continue."""

from __future__ import annotations

import json
from pathlib import Path

PROGRESS_NAME = "progress.json"


def tutorial_dir(output_dir: str | Path, project_name: str) -> Path:
    return Path(output_dir) / project_name


def progress_path(folder: Path) -> Path:
    return Path(folder) / PROGRESS_NAME


def load_progress(folder: Path) -> dict:
    path = progress_path(folder)
    if not path.is_file():
        return {"chapters": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"chapters": {}}
    if not isinstance(data, dict):
        return {"chapters": {}}
    data.setdefault("chapters", {})
    return data


def file_fingerprint(paths_and_contents) -> str:
    import hashlib

    digest = hashlib.sha256()
    for path, content in paths_and_contents or []:
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update((content or "").encode("utf-8"))
    return digest.hexdigest()[:16]


def save_chapter(folder: Path, filename: str, content: str, *, fingerprint: str | None = None) -> None:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(content, encoding="utf-8")
    progress = load_progress(folder)
    progress["chapters"][filename] = {"chars": len(content), "fingerprint": fingerprint}
    progress_path(folder).write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def load_saved_chapter(
    folder: Path,
    filename: str,
    *,
    min_chars: int = 200,
    fingerprint: str | None = None,
) -> str | None:
    path = Path(folder) / filename
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    if len(text) < min_chars:
        return None
    if fingerprint:
        progress = load_progress(folder)
        saved = (progress.get("chapters") or {}).get(filename) or {}
        if saved.get("fingerprint") and saved.get("fingerprint") != fingerprint:
            return None
    return text
