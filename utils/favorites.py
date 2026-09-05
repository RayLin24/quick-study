from __future__ import annotations

import json
from pathlib import Path

NAME = "favorites.json"


def load_favorites(output_dir: Path) -> list[str]:
    path = Path(output_dir) / NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    names = data.get("names") if isinstance(data, dict) else data
    return [str(item) for item in names] if isinstance(names, list) else []


def toggle_favorite(output_dir: Path, name: str) -> list[str]:
    names = load_favorites(output_dir)
    if name in names:
        names = [item for item in names if item != name]
    else:
        names.append(name)
    path = Path(output_dir) / NAME
    path.write_text(json.dumps({"names": names}, ensure_ascii=False, indent=2), encoding="utf-8")
    return names
