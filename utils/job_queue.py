"""Single-machine durable job queue (feature 4)."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path


def queue_path(output_dir: Path) -> Path:
    return Path(output_dir) / "job_queue.json"


def load_queue(output_dir: Path) -> list[dict]:
    path = queue_path(output_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    items = data.get("items") if isinstance(data, dict) else data
    return items if isinstance(items, list) else []


def save_queue(output_dir: Path, items: list[dict]) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    tmp = queue_path(output_dir).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(queue_path(output_dir))


def enqueue(output_dir: Path, payload: dict) -> dict:
    items = load_queue(output_dir)
    item = {
        "id": uuid.uuid4().hex[:12],
        "payload": {k: v for k, v in payload.items() if k != "github_token"},
        "enqueued_at": time.time(),
    }
    # Keep token only in memory of caller; persist a redacted copy.
    items.append(item)
    save_queue(output_dir, items)
    item["position"] = len(items)
    return item


def dequeue(output_dir: Path) -> dict | None:
    items = load_queue(output_dir)
    if not items:
        return None
    item = items.pop(0)
    save_queue(output_dir, items)
    return item
