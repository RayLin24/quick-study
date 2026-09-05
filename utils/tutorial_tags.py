"""Tag / group the local tutorial library (feature 22)."""

from __future__ import annotations

import json
from pathlib import Path

NAME = "library_tags.json"


def tags_path(output_dir: Path) -> Path:
    return Path(output_dir) / NAME


def load_tags(output_dir: Path) -> dict:
    path = tags_path(output_dir)
    if not path.is_file():
        return {"tutorials": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"tutorials": {}}
    data.setdefault("tutorials", {})
    return data


def save_tags(output_dir: Path, data: dict) -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    tags_path(output_dir).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def set_tags(output_dir: Path, tutorial: str, tags: list[str], group: str = "") -> dict:
    data = load_tags(output_dir)
    clean = [str(t).strip() for t in tags if str(t).strip()]
    data["tutorials"][tutorial] = {"tags": clean, "group": (group or "").strip()}
    save_tags(output_dir, data)
    return data["tutorials"][tutorial]


def list_groups(output_dir: Path) -> dict:
    data = load_tags(output_dir)
    groups: dict[str, list[str]] = {}
    for name, meta in (data.get("tutorials") or {}).items():
        key = (meta or {}).get("group") or "未分组"
        groups.setdefault(key, []).append(name)
    return {"groups": groups, "tutorials": data.get("tutorials") or {}}
