from __future__ import annotations

import json
import time
from pathlib import Path

NAME = "annotations.json"


def load_annotations(folder: Path) -> list[dict]:
    path = Path(folder) / NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def add_annotation(folder: Path, *, filename: str, quote: str, note: str) -> list[dict]:
    items = load_annotations(folder)
    items.append(
        {
            "filename": filename,
            "quote": (quote or "")[:400],
            "note": (note or "")[:800],
            "ts": int(time.time()),
        }
    )
    path = Path(folder) / NAME
    path.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    return items
