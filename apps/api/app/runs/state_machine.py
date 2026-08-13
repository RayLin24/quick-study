"""Run-level state transitions.

The transition tables and the checks themselves live on the model in
:mod:`app.db.models.execution`, because they are enforced as attribute validators: an
invariant that only holds when somebody calls the right function is not an invariant. This
module is the vocabulary of *operations* -- what starting, suspending or finishing a run
means -- and re-exports the tables so callers have one place to read.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.clock import utcnow
from app.db.models import Run
from app.db.models.enums import RunPhase, RunStatus
from app.db.models.execution import (
    ALLOWED_RUN_STATUS_TRANSITIONS,
    BACKWARD_PHASE_EXCEPTIONS,
    PHASE_ORDER,
    TERMINAL_RUN_STATUSES,
    IllegalRunPhase,
    IllegalRunStatus,
    RunStateError,
    assert_phase_change,
    assert_status_change,
    can_advance_phase,
    can_change_status,
    unchecked_run_state,
)

__all__ = [
    "ALLOWED_RUN_STATUS_TRANSITIONS",
    "BACKWARD_PHASE_EXCEPTIONS",
    "PHASE_ORDER",
    "TERMINAL_RUN_STATUSES",
    "IllegalRunPhase",
    "IllegalRunStatus",
    "RunStateError",
    "advance_phase",
    "assert_phase_change",
    "assert_status_change",
    "can_advance_phase",
    "can_change_status",
    "cancel_run",
    "fail_run",
    "record_run_usage",
    "resume_run",
    "start_run",
    "succeed_run",
    "suspend_run",
    "unchecked_run_state",
]


def advance_phase(run: Run, phase: RunPhase) -> None:
    assert_phase_change(run.phase, phase)
    run.phase = phase


def start_run(run: Run, *, phase: RunPhase | None = None, now: datetime | None = None) -> None:
    assert_status_change(run.status, RunStatus.RUNNING)
    if phase is not None:
        assert_phase_change(run.phase, phase)
    run.status = RunStatus.RUNNING
    run.started_at = now or utcnow()
    if phase is not None:
        run.phase = phase


def suspend_run(run: Run, *, phase: RunPhase | None = None, now: datetime | None = None) -> None:
    """Park a run that is waiting for a human decision, keeping it eligible to resume.

    There is no ``suspended_at`` column: a run may be parked and resumed many times, so
    ``now`` lands on ``updated_at``, which is what a reviewer's queue sorts by.
    """
    assert_status_change(run.status, RunStatus.SUSPENDED)
    if phase is not None:
        assert_phase_change(run.phase, phase)
        run.phase = phase
    run.status = RunStatus.SUSPENDED
    run.updated_at = now or utcnow()


def resume_run(run: Run, *, phase: RunPhase | None = None, now: datetime | None = None) -> None:
    """Put a parked run back to work; ``now`` records when it was picked up again."""
    assert_status_change(run.status, RunStatus.RUNNING)
    if phase is not None:
        assert_phase_change(run.phase, phase)
        run.phase = phase
    run.status = RunStatus.RUNNING
    run.updated_at = now or utcnow()


def succeed_run(run: Run, *, now: datetime | None = None) -> None:
    """Finish a run. Only a published run counts as successful."""
    assert_status_change(run.status, RunStatus.SUCCEEDED)
    if run.phase is not RunPhase.PUBLISH:
        raise IllegalRunPhase(f"a run in {run.phase.value} has not published anything yet")
    run.status = RunStatus.SUCCEEDED
    run.finished_at = now or utcnow()


def fail_run(
    run: Run,
    *,
    error_code: str,
    error_message: str,
    now: datetime | None = None,
) -> None:
    assert_status_change(run.status, RunStatus.FAILED)
    run.status = RunStatus.FAILED
    run.error_code = error_code
    run.error_message = error_message
    run.finished_at = now or utcnow()


def cancel_run(run: Run, *, now: datetime | None = None) -> None:
    assert_status_change(run.status, RunStatus.CANCELLED)
    run.status = RunStatus.CANCELLED
    run.finished_at = now or utcnow()


def record_run_usage(
    run: Run,
    *,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: Decimal = Decimal("0"),
) -> None:
    """Accumulate model usage so a cost ceiling can be enforced per run."""
    run.tokens_in += tokens_in
    run.tokens_out += tokens_out
    run.cost_usd = Decimal(run.cost_usd) + cost_usd
