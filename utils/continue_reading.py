"""Home-card helper for 'continue reading today' (feature 47)."""

from __future__ import annotations

import json
from pathlib import Path

from web.render import list_tutorials


def continue_card(output_dir: Path, progress: dict | None = None) -> dict | None:
    items = list_tutorials(output_dir)
    if not items:
        return None
    progress = progress or {}
    newest_path = None
    newest_ts = -1
    for path, ts in progress.items():
        try:
            stamp = float(ts)
        except (TypeError, ValueError):
            continue
        if stamp > newest_ts and "/t/" in str(path):
            newest_ts = stamp
            newest_path = str(path)
    if newest_path:
        parts = newest_path.split("/t/", 1)[-1].split("/")
        name = parts[0]
        filename = parts[1] if len(parts) > 1 else "index.md"
        return {"tutorial": name, "filename": filename, "href": newest_path, "source": "progress"}
    top = items[0]
    return {"tutorial": top["name"], "filename": "index.md", "href": f"/t/{top['name']}", "source": "recent"}


def load_progress_file(output_dir: Path) -> dict:
    path = Path(output_dir) / "continue.json"
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_progress_file(output_dir: Path, progress: dict) -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    (Path(output_dir) / "continue.json").write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    return progress
