"""Structured JSONL run logs for jq (feature 35)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from utils.log_context import merge_log_context


def jsonl_path(output_dir: Path) -> Path:
    return Path(output_dir) / "run.jsonl"


def emit_run(output_dir: Path, event: str, **fields) -> dict:
    rec = merge_log_context(ts=time.time(), event=event, **fields)
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    with jsonl_path(output_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def tail_run(output_dir: Path, *, limit: int = 50) -> list[dict]:
    path = jsonl_path(output_dir)
    if not path.is_file():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    out = []
    for line in lines[-max(1, int(limit)) :]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
