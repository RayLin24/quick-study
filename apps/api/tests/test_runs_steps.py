from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa
from conftest import make_run
from sqlalchemy.orm import Session

from app.db.models import Artifact, Run, Step
from app.db.models.enums import ArtifactKind, RunPhase, StepStatus
from app.runs.steps import (
    IDEMPOTENCY_KEY_MAX_LENGTH,
    LEASE_OWNER_MAX_LENGTH,
    ClaimOutcome,
    LeaseLost,
    StepFailed,
    build_idempotency_key,
    claim_step,
    complete_step,
    ensure_step,
    fail_step,
    heartbeat_step,
    new_lease_owner,
    run_step,
)

NOW = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)
LEASE = timedelta(minutes=5)
WORKER_A = "worker-a@host"
WORKER_B = "worker-b@host"


def new_step(db: Session, run: Run | None = None, *, max_attempts: int = 3) -> Step:
    run = run or make_run(db)
    return ensure_step(
        db,
        run=run,
        name="snapshot",
        phase=RunPhase.SNAPSHOT,
        sequence=1,
        max_attempts=max_attempts,
    )


def test_idempotency_key_is_deterministic_for_the_same_inputs() -> None:
    first = build_idempotency_key("run-1", "chapters", "chapter-3")
    second = build_idempotency_key("run-1", "chapters", "chapter-3")

    assert first == second
    assert first != build_idempotency_key("run-1", "chapters", "chapter-4")


def test_idempotency_key_stays_inside_the_indexed_column_width() -> None:
    key = build_idempotency_key("run-1", "chapters", "x" * 500)

    assert len(key) <= IDEMPOTENCY_KEY_MAX_LENGTH
    assert key == build_idempotency_key("run-1", "chapters", "x" * 500)
    assert key != build_idempotency_key("run-1", "chapters", "x" * 501)


def test_idempotency_key_refuses_to_be_empty() -> None:
    with pytest.raises(ValueError):
        build_idempotency_key()


def test_ensure_step_creates_a_pending_step_that_has_not_been_attempted(db: Session) -> None:
    step = new_step(db)

    assert step.status is StepStatus.PENDING
    assert step.attempt == 0
    assert step.lease_owner is None
    assert step.idempotency_key


def test_a_redelivered_message_reuses_the_existing_step(db: Session) -> None:
    run = make_run(db)
    first = new_step(db, run)
    db.commit()

    second = new_step(db, run)

    assert second.id == first.id
    assert db.scalar(sa.select(sa.func.count()).select_from(Step)) == 1


def test_two_runs_get_their_own_step_for_the_same_logical_work(db: Session) -> None:
    from conftest import make_project

    project = make_project(db)
    first_run = make_run(db, project=project)
    second_run = make_run(db, project=project)

    first = new_step(db, first_run)
    second = new_step(db, second_run)

    assert first.id != second.id
    assert first.idempotency_key != second.idempotency_key


def test_claiming_a_pending_step_takes_a_lease_and_counts_the_attempt(db: Session) -> None:
    step = new_step(db)

    claim = claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    assert claim.outcome is ClaimOutcome.CLAIMED
    assert claim.claimed is True
    assert step.status is StepStatus.RUNNING
    assert step.attempt == 1
    assert step.lease_owner == WORKER_A
    assert step.lease_expires_at == NOW + LEASE
    assert step.started_at == NOW


def test_a_second_worker_cannot_claim_a_step_under_a_live_lease(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    db.commit()

    claim = claim_step(db, step, owner=WORKER_B, lease=LEASE, now=NOW + timedelta(minutes=1))

    assert claim.outcome is ClaimOutcome.LEASED_ELSEWHERE
    assert claim.claimed is False
    assert step.lease_owner == WORKER_A
    assert step.attempt == 1


def test_the_lease_holder_re_entering_extends_the_lease_without_a_new_attempt(
    db: Session,
) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    later = NOW + timedelta(minutes=2)
    claim = claim_step(db, step, owner=WORKER_A, lease=LEASE, now=later)

    assert claim.outcome is ClaimOutcome.CLAIMED
    assert step.attempt == 1
    assert step.lease_expires_at == later + LEASE


def test_an_expired_lease_lets_another_worker_retry_after_a_hard_kill(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    db.commit()

    after_expiry = NOW + LEASE + timedelta(seconds=1)
    claim = claim_step(db, step, owner=WORKER_B, lease=LEASE, now=after_expiry)

    assert claim.outcome is ClaimOutcome.CLAIMED
    assert step.lease_owner == WORKER_B
    assert step.attempt == 2


def test_a_succeeded_step_is_never_re_executed_by_a_duplicate_delivery(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    complete_step(db, step, owner=WORKER_A, now=NOW + timedelta(seconds=30))
    db.commit()

    claim = claim_step(db, step, owner=WORKER_B, lease=LEASE, now=NOW + timedelta(minutes=10))

    assert claim.outcome is ClaimOutcome.ALREADY_SUCCEEDED
    assert claim.claimed is False
    assert step.status is StepStatus.SUCCEEDED
    assert step.attempt == 1


def test_claiming_stops_and_fails_the_step_once_the_attempts_run_out(db: Session) -> None:
    step = new_step(db, max_attempts=2)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    fail_step(db, step, owner=WORKER_A, error_code="timeout", error_message="model timed out")
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW + timedelta(minutes=1))
    fail_step(db, step, owner=WORKER_A, error_code="timeout", error_message="model timed out")

    claim = claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW + timedelta(minutes=2))

    assert claim.outcome is ClaimOutcome.EXHAUSTED
    assert step.status is StepStatus.FAILED
    assert step.attempt == 2


def test_a_crash_on_the_final_attempt_leaves_the_step_failed_rather_than_running(
    db: Session,
) -> None:
    """Nobody may retry it, so it must not sit in ``running`` forever."""
    step = new_step(db, max_attempts=1)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    db.commit()

    claim = claim_step(
        db, step, owner=WORKER_B, lease=LEASE, now=NOW + LEASE + timedelta(seconds=1)
    )

    assert claim.outcome is ClaimOutcome.EXHAUSTED
    assert step.status is StepStatus.FAILED
    assert step.error_code == "max_attempts_exhausted"
    assert step.lease_owner is None
    assert step.finished_at is not None


def test_completing_a_step_records_usage_and_releases_the_lease(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    finished = NOW + timedelta(seconds=42)

    complete_step(
        db,
        step,
        owner=WORKER_A,
        model="deepseek-chat",
        prompt_hash="a" * 64,
        tokens_in=1200,
        tokens_out=800,
        cost_usd=Decimal("0.012500"),
        now=finished,
    )

    assert step.status is StepStatus.SUCCEEDED
    assert step.finished_at == finished
    assert step.lease_owner is None
    assert step.lease_expires_at is None
    assert step.model == "deepseek-chat"
    assert step.tokens_in == 1200
    assert step.cost_usd == Decimal("0.012500")
    assert step.error_code is None


def test_a_worker_that_lost_its_lease_cannot_mark_the_step_succeeded(db: Session) -> None:
    """The whole point of the lease: a resurrected worker must not report stale success."""
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    db.commit()
    stolen_at = NOW + LEASE + timedelta(seconds=1)
    claim_step(db, step, owner=WORKER_B, lease=LEASE, now=stolen_at)
    db.commit()

    with pytest.raises(LeaseLost):
        complete_step(db, step, owner=WORKER_A, now=stolen_at)

    db.rollback()
    reloaded = db.get(Step, step.id)
    assert reloaded is not None
    assert reloaded.status is StepStatus.RUNNING
    assert reloaded.lease_owner == WORKER_B


def test_completing_a_step_nobody_claimed_is_refused(db: Session) -> None:
    step = new_step(db)

    with pytest.raises(LeaseLost):
        complete_step(db, step, owner=WORKER_A, now=NOW)

    assert step.status is StepStatus.PENDING


def test_completing_an_already_finished_step_is_refused(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    complete_step(db, step, owner=WORKER_A, now=NOW)

    with pytest.raises(LeaseLost):
        complete_step(db, step, owner=WORKER_A, now=NOW)


def test_a_retryable_failure_returns_the_step_to_the_queue(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    status = fail_step(
        db,
        step,
        owner=WORKER_A,
        error_code="model_timeout",
        error_message="deepseek timed out",
        now=NOW,
    )

    assert status is StepStatus.PENDING
    assert step.status is StepStatus.PENDING
    assert step.lease_owner is None
    assert step.error_code == "model_timeout"
    assert step.attempt == 1


def test_a_retryable_failure_on_the_last_attempt_is_final(db: Session) -> None:
    step = new_step(db, max_attempts=1)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    status = fail_step(db, step, owner=WORKER_A, error_code="timeout", error_message="gave up")

    assert status is StepStatus.FAILED
    assert step.status is StepStatus.FAILED
    assert step.finished_at is not None


def test_a_permanent_failure_skips_the_remaining_attempts(db: Session) -> None:
    step = new_step(db, max_attempts=5)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    status = fail_step(
        db,
        step,
        owner=WORKER_A,
        error_code="invalid_source",
        error_message="not a public repository",
        retryable=False,
    )

    assert status is StepStatus.FAILED
    assert step.attempt == 1


def test_only_the_lease_holder_may_report_a_failure(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    with pytest.raises(LeaseLost):
        fail_step(db, step, owner=WORKER_B, error_code="oops", error_message="not mine")

    assert step.status is StepStatus.RUNNING


def test_a_heartbeat_extends_the_lease_of_a_long_running_step(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    later = NOW + timedelta(minutes=4)
    heartbeat_step(db, step, owner=WORKER_A, lease=LEASE, now=later)

    assert step.lease_expires_at == later + LEASE
    assert step.attempt == 1


def test_only_the_lease_holder_may_heartbeat(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)

    with pytest.raises(LeaseLost):
        heartbeat_step(db, step, owner=WORKER_B, lease=LEASE, now=NOW)


def test_two_workers_racing_for_the_same_pending_step_produce_one_winner(
    db: Session,
    second_db: Session,
) -> None:
    step = new_step(db)
    db.commit()

    first = claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    db.commit()
    contender = second_db.get(Step, step.id)
    assert contender is not None
    second = claim_step(second_db, contender, owner=WORKER_B, lease=LEASE, now=NOW)

    assert [first.outcome, second.outcome] == [
        ClaimOutcome.CLAIMED,
        ClaimOutcome.LEASED_ELSEWHERE,
    ]


def test_idempotency_key_parts_cannot_collide_through_the_separator() -> None:
    """Two different logical units must never share a key, or one is skipped as done."""
    assert build_idempotency_key("run-1", "chapter:3") != build_idempotency_key(
        "run-1", "chapter", "3"
    )
    assert build_idempotency_key("a:b", "c") != build_idempotency_key("a", "b:c")


def test_idempotency_key_survives_a_part_that_looks_like_an_escape() -> None:
    assert build_idempotency_key("a\\", "b") != build_idempotency_key("a", "b")
    assert build_idempotency_key("a\\:b") != build_idempotency_key("a\\", "b")


def test_every_lease_owner_names_exactly_one_execution() -> None:
    assert new_lease_owner() != new_lease_owner()
    assert new_lease_owner("worker@node") != new_lease_owner("worker@node")


def test_a_lease_owner_keeps_its_label_and_fits_the_column() -> None:
    owner = new_lease_owner("worker@node")

    assert owner.startswith("worker@node")
    assert len(owner) <= LEASE_OWNER_MAX_LENGTH
    assert len(new_lease_owner("w" * 500)) <= LEASE_OWNER_MAX_LENGTH


def test_two_workers_sharing_a_hostname_still_exclude_each_other(db: Session) -> None:
    """``celery --hostname=worker@%h`` gives every worker on a node the same name."""
    step = new_step(db)

    first = claim_step(db, step, owner=new_lease_owner("worker@node"), lease=LEASE, now=NOW)
    second = claim_step(db, step, owner=new_lease_owner("worker@node"), lease=LEASE, now=NOW)

    assert [first.outcome, second.outcome] == [
        ClaimOutcome.CLAIMED,
        ClaimOutcome.LEASED_ELSEWHERE,
    ]


def test_completing_without_naming_the_model_keeps_what_the_attempt_recorded(
    db: Session,
) -> None:
    """Usage written while the step ran must survive the statement that finishes it."""
    step = new_step(db)
    claim_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW)
    step.model = "deepseek-chat"
    step.prompt_hash = "b" * 64
    db.flush()

    complete_step(db, step, owner=WORKER_A, now=NOW)

    assert step.model == "deepseek-chat"
    assert step.prompt_hash == "b" * 64


def test_run_step_commits_the_claim_before_the_body_runs(db: Session) -> None:
    """A claim that is not committed excludes nobody, so the work must not start first."""
    step = new_step(db)
    db.commit()

    with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW) as execution:
        assert execution is not None
        db.rollback()
        reloaded = db.get(Step, step.id)
        assert reloaded is not None
        assert reloaded.status is StepStatus.RUNNING
        assert reloaded.lease_owner == WORKER_A


def test_run_step_publishes_the_artifacts_and_the_success_together(db: Session) -> None:
    step = new_step(db)
    db.commit()

    with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW) as execution:
        assert execution is not None
        db.add(
            Artifact(
                project_id=step.project_id,
                run_id=step.run_id,
                step_id=step.id,
                kind=ArtifactKind.RAW_HTML,
                sha256="a" * 64,
                storage_path="aa/aa/" + "a" * 64,
            )
        )
        execution.record_usage(model="deepseek-chat", tokens_in=10, tokens_out=20)

    db.rollback()
    reloaded = db.get(Step, step.id)
    assert reloaded is not None
    assert reloaded.status is StepStatus.SUCCEEDED
    assert reloaded.model == "deepseek-chat"
    assert reloaded.tokens_in == 10
    assert db.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 1


def test_run_step_hands_the_work_to_nobody_when_the_lease_is_held(db: Session) -> None:
    step = new_step(db)
    claim_step(db, step, owner=WORKER_B, lease=LEASE, now=NOW)
    db.commit()

    with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW) as execution:
        assert execution is None


def test_run_step_records_a_failure_and_re_raises_what_the_body_raised(db: Session) -> None:
    step = new_step(db)
    db.commit()

    with pytest.raises(RuntimeError, match="the model went away"):
        with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW):
            raise RuntimeError("the model went away")

    db.rollback()
    reloaded = db.get(Step, step.id)
    assert reloaded is not None
    assert reloaded.status is StepStatus.PENDING
    assert reloaded.error_code == "RuntimeError"
    assert reloaded.lease_owner is None


def test_run_step_discards_the_half_written_artifacts_of_a_failed_attempt(db: Session) -> None:
    step = new_step(db)
    db.commit()

    with pytest.raises(RuntimeError):
        with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW):
            db.add(
                Artifact(
                    project_id=step.project_id,
                    kind=ArtifactKind.RAW_HTML,
                    sha256="c" * 64,
                    storage_path="cc/cc/" + "c" * 64,
                )
            )
            db.flush()
            raise RuntimeError("died after writing half of it")

    db.rollback()
    assert db.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 0


def test_run_step_spends_no_further_attempt_on_a_permanent_failure(db: Session) -> None:
    step = new_step(db, max_attempts=5)
    db.commit()

    with pytest.raises(StepFailed):
        with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW):
            raise StepFailed("invalid_source", "not a public repository", retryable=False)

    db.rollback()
    reloaded = db.get(Step, step.id)
    assert reloaded is not None
    assert reloaded.status is StepStatus.FAILED
    assert reloaded.error_code == "invalid_source"


def test_a_heartbeat_taken_through_the_execution_extends_the_lease(db: Session) -> None:
    step = new_step(db)
    db.commit()

    with run_step(db, step, owner=WORKER_A, lease=LEASE, now=NOW) as execution:
        assert execution is not None
        execution.heartbeat(now=NOW + timedelta(minutes=4))

        assert execution.step.lease_expires_at == NOW + timedelta(minutes=4) + LEASE
