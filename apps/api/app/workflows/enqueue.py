"""Queue a wake-up. Celery only carries identifiers; MySQL holds the facts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def enqueue_start(run_id: str) -> None:
    from app.workflows.tasks import start_tutorial_run

    start_tutorial_run.delay(run_id)


def enqueue_resume(run_id: str, decision: Mapping[str, Any]) -> None:
    from app.workflows.tasks import resume_tutorial_run

    resume_tutorial_run.delay(run_id, dict(decision))
