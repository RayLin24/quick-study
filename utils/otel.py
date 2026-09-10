"""Optional lightweight OpenTelemetry-style traces (feature 34). Off by default."""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from utils.log_context import merge_log_context


def otel_enabled() -> bool:
    return (os.getenv("OTEL_TRACES") or "").strip().lower() in {"1", "true", "yes"}


def traces_path(output_dir: Path | None = None) -> Path:
    return Path(output_dir or os.getenv("QUICK_STUDY_OUTPUT") or "output") / "traces.jsonl"


@contextmanager
def span(name: str, *, output_dir: Path | None = None, attributes: dict | None = None):
    started = time.time()
    rec = merge_log_context(
        trace_id=uuid.uuid4().hex,
        name=name,
        attributes=attributes or {},
        start=started,
    )
    try:
        yield rec
        rec["status"] = "ok"
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = str(exc)
        raise
    finally:
        rec["end"] = time.time()
        rec["duration_ms"] = int((rec["end"] - started) * 1000)
        if otel_enabled():
            path = traces_path(output_dir)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
