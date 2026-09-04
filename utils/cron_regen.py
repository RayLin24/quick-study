from __future__ import annotations

import json
from pathlib import Path

SCHEDULE_NAME = "cron_regen.json"


def load_schedule(output_dir: Path) -> list[dict]:
    path = Path(output_dir) / SCHEDULE_NAME
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("jobs") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def save_schedule(output_dir: Path, jobs: list[dict]) -> list[dict]:
    path = Path(output_dir) / SCHEDULE_NAME
    cleaned = []
    for item in jobs:
        url = str((item or {}).get("repo_url") or "").strip()
        if not url:
            continue
        cleaned.append(
            {
                "repo_url": url,
                "cron": str(item.get("cron") or "0 3 * * 1"),
                "incremental": bool(item.get("incremental", True)),
                "name": str(item.get("name") or "").strip() or None,
            }
        )
    path.write_text(json.dumps({"jobs": cleaned}, ensure_ascii=False, indent=2), encoding="utf-8")
    return cleaned


def cron_command(python_exe: str, main_py: Path, job: dict) -> list[str]:
    cmd = [python_exe, str(main_py), "--repo", job["repo_url"], "--incremental"]
    if job.get("name"):
        cmd += ["--name", job["name"]]
    return cmd
