from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import make_run
from sqlalchemy.orm import Session

from app.db.models.enums import RunPhase, RunStatus
from app.runs.state_machine import (
    ALLOWED_RUN_STATUS_TRANSITIONS,
    TERMINAL_RUN_STATUSES,
    IllegalRunPhase,
    IllegalRunStatus,
    advance_phase,
    can_change_status,
    cancel_run,
    fail_run,
    record_run_usage,
    resume_run,
    start_run,
    succeed_run,
    suspend_run,
    unchecked_run_state,
)

FIXED_NOW = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)

LEGAL_STATUS_CHANGES = [
    (RunStatus.PENDING, RunStatus.RUNNING),
    (RunStatus.PENDING, RunStatus.CANCELLED),
    (RunStatus.RUNNING, RunStatus.SUSPENDED),
    (RunStatus.RUNNING, RunStatus.SUCCEEDED),
    (RunStatus.RUNNING, RunStatus.FAILED),
    (RunStatus.RUNNING, RunStatus.CANCELLED),
    (RunStatus.SUSPENDED, RunStatus.RUNNING),
    (RunStatus.SUSPENDED, RunStatus.FAILED),
    (RunStatus.SUSPENDED, RunStatus.CANCELLED),
]

ILLEGAL_STATUS_CHANGES = [
    (RunStatus.PENDING, RunStatus.SUCCEEDED),
    (RunStatus.PENDING, RunStatus.SUSPENDED),
    (RunStatus.PENDING, RunStatus.PENDING),
    (RunStatus.RUNNING, RunStatus.RUNNING),
    (RunStatus.RUNNING, RunStatus.PENDING),
    (RunStatus.SUSPENDED, RunStatus.SUCCEEDED),
    (RunStatus.SUSPENDED, RunStatus.SUSPENDED),
    (RunStatus.SUCCEEDED, RunStatus.RUNNING),
    (RunStatus.FAILED, RunStatus.RUNNING),
    (RunStatus.FAILED, RunStatus.PENDING),
    (RunStatus.CANCELLED, RunStatus.RUNNING),
]


def test_status_vocabulary_describes_execution_only_and_never_a_pipeline_stage() -> None:
    """A stage name leaking into ``status`` is what ``phase`` exists to prevent."""
    phase_names = {phase.value for phase in RunPhase}

    assert {status.value for status in RunStatus}.isdisjoint(phase_names)


def test_every_status_has_an_explicit_transition_set() -> None:
    assert set(ALLOWED_RUN_STATUS_TRANSITIONS) == set(RunStatus)


def test_terminal_statuses_allow_nothing_further() -> None:
    assert TERMINAL_RUN_STATUSES == frozenset(
        {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
    )
    for status in TERMINAL_RUN_STATUSES:
        assert ALLOWED_RUN_STATUS_TRANSITIONS[status] == frozenset()


@pytest.mark.parametrize(("current", "target"), LEGAL_STATUS_CHANGES)
def test_documented_status_changes_are_allowed(current: RunStatus, target: RunStatus) -> None:
    assert can_change_status(current, target) is True


@pytest.mark.parametrize(("current", "target"), ILLEGAL_STATUS_CHANGES)
def test_undocumented_status_changes_are_refused(current: RunStatus, target: RunStatus) -> None:
    assert can_change_status(current, target) is False


def test_start_run_marks_it_running_and_stamps_the_start(db: Session) -> None:
    run = make_run(db)

    start_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.RUNNING
    assert run.started_at == FIXED_NOW
    assert run.finished_at is None


def test_start_run_moves_out_of_the_queued_phase(db: Session) -> None:
    run = make_run(db)

    start_run(run, phase=RunPhase.DISCOVER, now=FIXED_NOW)

    assert run.phase is RunPhase.DISCOVER


def test_starting_a_running_run_is_refused_and_changes_nothing(db: Session) -> None:
    run = make_run(db)
    start_run(run, now=FIXED_NOW)

    with pytest.raises(IllegalRunStatus):
        start_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.RUNNING
    assert run.started_at == FIXED_NOW


def test_a_run_suspends_while_it_waits_for_a_human_and_resumes_afterwards(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)

    suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=FIXED_NOW)

    assert run.status is RunStatus.SUSPENDED
    assert run.phase is RunPhase.HUMAN_INTERRUPT
    assert run.finished_at is None

    resume_run(run, phase=RunPhase.CHAPTERS, now=FIXED_NOW)

    assert run.status is RunStatus.RUNNING
    assert run.phase is RunPhase.CHAPTERS


def test_a_rejected_outline_sends_the_run_back_to_the_outline_phase(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)
    suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=FIXED_NOW)

    resume_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)

    assert run.phase is RunPhase.OUTLINE


def test_phases_only_move_forward_through_the_graph(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.DISCOVER, now=FIXED_NOW)

    for phase in (RunPhase.SNAPSHOT, RunPhase.PARSE, RunPhase.INDEX, RunPhase.ANALYZE):
        advance_phase(run, phase)

    assert run.phase is RunPhase.ANALYZE


def test_re_entering_the_current_phase_is_allowed_because_steps_retry(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.CHAPTERS, now=FIXED_NOW)

    advance_phase(run, RunPhase.CHAPTERS)

    assert run.phase is RunPhase.CHAPTERS


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunPhase.CHAPTERS, RunPhase.SNAPSHOT),
        (RunPhase.PUBLISH, RunPhase.CHAPTERS),
        (RunPhase.INDEX, RunPhase.DISCOVER),
        (RunPhase.OUTLINE, RunPhase.QUEUED),
    ],
)
def test_phases_never_walk_backwards(db: Session, current: RunPhase, target: RunPhase) -> None:
    run = make_run(db)
    start_run(run, phase=current, now=FIXED_NOW)

    with pytest.raises(IllegalRunPhase):
        advance_phase(run, target)

    assert run.phase is current


def test_a_run_can_only_succeed_once_it_has_published(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.VALIDATE, now=FIXED_NOW)

    with pytest.raises(IllegalRunPhase):
        succeed_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.RUNNING

    advance_phase(run, RunPhase.PUBLISH)
    succeed_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.SUCCEEDED
    assert run.finished_at == FIXED_NOW


def test_failing_a_run_records_a_diagnosable_reason(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.SNAPSHOT, now=FIXED_NOW)

    fail_run(run, error_code="fetch_blocked", error_message="robots.txt denied", now=FIXED_NOW)

    assert run.status is RunStatus.FAILED
    assert run.phase is RunPhase.SNAPSHOT
    assert run.error_code == "fetch_blocked"
    assert run.error_message == "robots.txt denied"
    assert run.finished_at == FIXED_NOW


def test_a_suspended_run_can_be_cancelled_by_a_reviewer(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)
    suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=FIXED_NOW)

    cancel_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.CANCELLED
    assert run.finished_at == FIXED_NOW


def test_a_finished_run_cannot_be_failed_or_cancelled_again(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.PUBLISH, now=FIXED_NOW)
    succeed_run(run, now=FIXED_NOW)

    with pytest.raises(IllegalRunStatus):
        fail_run(run, error_code="late", error_message="too late", now=FIXED_NOW)
    with pytest.raises(IllegalRunStatus):
        cancel_run(run, now=FIXED_NOW)

    assert run.status is RunStatus.SUCCEEDED


def test_usage_accumulates_across_steps(db: Session) -> None:
    run = make_run(db)

    record_run_usage(run, tokens_in=120, tokens_out=340, cost_usd=Decimal("0.004500"))
    record_run_usage(run, tokens_in=80, tokens_out=60, cost_usd=Decimal("0.001500"))

    assert run.tokens_in == 200
    assert run.tokens_out == 400
    assert run.cost_usd == Decimal("0.006000")


def test_an_optional_phase_may_be_skipped(db: Session) -> None:
    """A tutorial with nothing to draw goes from chapters straight to validation."""
    run = make_run(db)
    start_run(run, phase=RunPhase.CHAPTERS, now=FIXED_NOW)

    advance_phase(run, RunPhase.VALIDATE)

    assert run.phase is RunPhase.VALIDATE


def test_suspending_records_when_the_run_was_parked(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)
    parked_at = FIXED_NOW + timedelta(minutes=5)

    suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=parked_at)
    db.flush()

    assert run.updated_at == parked_at


def test_resuming_records_when_the_run_was_picked_up_again(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.OUTLINE, now=FIXED_NOW)
    suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=FIXED_NOW)
    resumed_at = FIXED_NOW + timedelta(hours=3)

    resume_run(run, phase=RunPhase.CHAPTERS, now=resumed_at)
    db.flush()

    assert run.updated_at == resumed_at


def test_a_phase_cannot_walk_backwards_by_assigning_the_column(db: Session) -> None:
    """The invariant has to hold for code that never calls this module."""
    run = make_run(db)
    start_run(run, phase=RunPhase.CHAPTERS, now=FIXED_NOW)

    with pytest.raises(IllegalRunPhase):
        run.phase = RunPhase.SNAPSHOT

    assert run.phase is RunPhase.CHAPTERS


def test_a_finished_run_cannot_be_revived_by_assigning_the_column(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.PUBLISH, now=FIXED_NOW)
    succeed_run(run, now=FIXED_NOW)

    with pytest.raises(IllegalRunStatus):
        run.status = RunStatus.RUNNING

    assert run.status is RunStatus.SUCCEEDED


def test_the_one_documented_way_back_stays_open_to_a_plain_assignment(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.HUMAN_INTERRUPT, now=FIXED_NOW)

    run.phase = RunPhase.OUTLINE

    assert run.phase is RunPhase.OUTLINE


def test_a_fixture_can_still_build_any_state_through_the_controlled_path(db: Session) -> None:
    """Tests and repair tooling need states the pipeline cannot reach on its own."""
    run = make_run(db, status=RunStatus.FAILED, phase=RunPhase.PUBLISH)

    with unchecked_run_state():
        run.phase = RunPhase.QUEUED
        run.status = RunStatus.RUNNING

    assert run.phase is RunPhase.QUEUED
    assert run.status is RunStatus.RUNNING


def test_the_controlled_path_closes_again_afterwards(db: Session) -> None:
    run = make_run(db)
    with unchecked_run_state():
        run.phase = RunPhase.PUBLISH

    with pytest.raises(IllegalRunPhase):
        run.phase = RunPhase.OUTLINE


def test_state_changes_persist_through_a_flush(db: Session) -> None:
    run = make_run(db)
    start_run(run, phase=RunPhase.DISCOVER, now=FIXED_NOW)
    db.commit()
    db.expire_all()

    reloaded = db.get(type(run), run.id)

    assert reloaded is not None
    assert reloaded.status is RunStatus.RUNNING
    assert reloaded.phase is RunPhase.DISCOVER
    assert reloaded.started_at == FIXED_NOW
