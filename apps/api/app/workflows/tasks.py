"""Celery tasks. They wake a run up; they do not decide anything.

A task takes an identifier, hands it to the runner and returns a summary small enough to
be a queue result. No business fact travels through Redis: the run's status, phase,
artifacts, cost and errors are read from MySQL, so a message that is lost, duplicated or
delivered out of order cannot change what the system believes.
"""

from __future__ import annotations

from typing import Any

from app.db.session import session_scope
from app.worker import celery_app
from app.workflows.checkpointing import build_checkpointer_provider
from app.workflows.tutorial.runner import RunOutcome, TutorialRunner


def build_runner(owner: str = "celery") -> TutorialRunner:
    """Assemble a runner from the deployment's configuration."""
    return TutorialRunner(
        provider=build_checkpointer_provider(),
        session_factory=session_scope,
        owner=owner,
    )


@celery_app.task(name="app.workflows.start_tutorial_run")
def start_tutorial_run(run_id: str) -> dict[str, Any]:
    return _summary(build_runner().start(run_id))


@celery_app.task(name="app.workflows.resume_tutorial_run")
def resume_tutorial_run(run_id: str, decision: dict[str, Any]) -> dict[str, Any]:
    return _summary(build_runner().resume(run_id, decision))


def _summary(outcome: RunOutcome) -> dict[str, Any]:
    """Report where the run got to, and what it is waiting for if it stopped."""
    return {
        "run_id": outcome.run_id,
        "status": outcome.status.value,
        "phase": outcome.phase.value,
        "awaiting": outcome.interrupt["kind"] if outcome.interrupt else None,
        "skipped": outcome.skipped,
    }
