from __future__ import annotations

import json
from pathlib import Path

NAME = "workbench.json"


def workbench_path(output_dir: Path) -> Path:
    return Path(output_dir) / NAME


def load_workbench(output_dir: Path) -> list[dict]:
    path = workbench_path(output_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("repos") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def save_workbench(output_dir: Path, repos: list[dict]) -> list[dict]:
    path = workbench_path(output_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = []
    for item in repos:
        url = str((item or {}).get("repo_url") or "").strip()
        if not url:
            continue
        cleaned.append(
            {
                "repo_url": url,
                "name": str(item.get("name") or "").strip() or None,
                "include": str(item.get("include") or "").strip() or None,
            }
        )
    path.write_text(json.dumps({"repos": cleaned}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned
