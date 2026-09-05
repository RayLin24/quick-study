"""Export / import generation parameter presets as JSON (feature 48)."""

from __future__ import annotations

import json
from pathlib import Path

KEYS = (
    "source_type",
    "repo_url",
    "local_dir",
    "language",
    "max_abstractions",
    "include",
    "exclude",
    "max_size",
    "strategy",
    "overview_only",
    "resume",
    "incremental",
    "bilingual",
    "pagerank_order",
    "learning_goal",
)


def dump_preset(payload: dict) -> dict:
    return {k: payload.get(k) for k in KEYS if payload.get(k) not in (None, "", [])}


def save_preset(output_dir: Path, name: str, payload: dict) -> dict:
    data = dump_preset(payload)
    dest = Path(output_dir) / "presets"
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"name": name, "path": str(path), "preset": data}


def load_preset(output_dir: Path, name: str) -> dict:
    path = Path(output_dir) / "presets" / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(name)
    return json.loads(path.read_text(encoding="utf-8"))


def list_presets(output_dir: Path) -> list[str]:
    dest = Path(output_dir) / "presets"
    if not dest.is_dir():
        return []
    return sorted(p.stem for p in dest.glob("*.json"))
