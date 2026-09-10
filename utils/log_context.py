"""Correlate job_id / tutorial / stage on every OTEL / JSONL line (#42)."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_job_id: ContextVar[str] = ContextVar("qs_job_id", default="")
_tutorial: ContextVar[str] = ContextVar("qs_tutorial", default="")
_stage: ContextVar[str] = ContextVar("qs_stage", default="")


def bind_log_context(
    *,
    job_id: str | None = None,
    tutorial: str | None = None,
    stage: str | None = None,
) -> None:
    if job_id is not None:
        _job_id.set(str(job_id))
    if tutorial is not None:
        _tutorial.set(str(tutorial))
    if stage is not None:
        _stage.set(str(stage))


def current_log_context() -> dict:
    """Always include the three keys (empty string when unbound)."""
    return {
        "job_id": _job_id.get() or "",
        "tutorial": _tutorial.get() or "",
        "stage": _stage.get() or "",
    }


def merge_log_context(**fields) -> dict:
    rec = current_log_context()
    for key in ("job_id", "tutorial", "stage"):
        value = fields.pop(key, None)
        if value not in (None, ""):
            rec[key] = str(value)
    rec.update(fields)
    return rec


@contextmanager
def log_context(*, job_id: str | None = None, tutorial: str | None = None, stage: str | None = None):
    tokens = []
    if job_id is not None:
        tokens.append((_job_id, _job_id.set(str(job_id))))
    if tutorial is not None:
        tokens.append((_tutorial, _tutorial.set(str(tutorial))))
    if stage is not None:
        tokens.append((_stage, _stage.set(str(stage))))
    try:
        yield current_log_context()
    finally:
        for var, token in reversed(tokens):
            var.reset(token)
