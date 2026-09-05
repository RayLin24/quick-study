"""Persist finished jobs for the history page (feature 5)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from utils.provider_cost import estimate_from_usage
from utils.redact import redact_text


def history_path(output_dir: Path) -> Path:
    return Path(output_dir) / "job_history.jsonl"


def append_history(output_dir: Path, snap: dict, *, started_at: float | None = None) -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    ended = time.time()
    duration = None
    if started_at:
        duration = round(ended - float(started_at), 2)
    record = {
        "id": snap.get("id"),
        "status": snap.get("status"),
        "output_name": snap.get("output_name"),
        "error": redact_text(snap.get("error") or "") or None,
        "usage": snap.get("usage"),
        "cost": estimate_from_usage(snap.get("usage")),
        "file_count": snap.get("file_count"),
        "source_type": snap.get("source_type"),
        "step": snap.get("step"),
        "started_at": started_at,
        "ended_at": ended,
        "duration_seconds": duration,
    }
    with history_path(output_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def list_history(output_dir: Path, *, limit: int = 50) -> list[dict]:
    path = history_path(output_dir)
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    items.reverse()
    return items[: max(1, int(limit))]
