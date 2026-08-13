"""The lease contract against real MySQL, on connections that genuinely contend.

The SQLite suite shares one connection between its sessions, so what look like two workers
there are really one, executed in order. Nothing about row locking or ``rowcount`` is
tested by that. These tests give each worker its own connection to MySQL 8.4 and let the
database arbitrate; they skip without ``QUICKSTUDY_TEST_MYSQL_URL``.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_run
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Step
from app.db.models.enums import RunPhase, StepStatus
from app.runs.steps import (
    ClaimOutcome,
    LeaseLost,
    claim_step,
    complete_step,
    ensure_step,
    heartbeat_step,
    new_lease_owner,
)

NOW = datetime(2026, 5, 6, 7, 8, 9, tzinfo=UTC)
LEASE = timedelta(minutes=5)
OWNER_A = new_lease_owner("worker-a")
OWNER_B = new_lease_owner("worker-b")
CONTENDERS = 6


@pytest.fixture
def sessions(migrated_mysql_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=migrated_mysql_engine, expire_on_commit=False, future=True)


@pytest.fixture
def worker_a(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as session:
        yield session


@pytest.fixture
def worker_b(sessions: sessionmaker[Session]) -> Iterator[Session]:
    """A second worker on its own connection, so the database decides who wins."""
    with sessions() as session:
        yield session


def seed_step(session: Session, *, max_attempts: int = 3) -> Step:
    step = ensure_step(
        session,
        run=make_run(session),
        name="chapter",
        phase=RunPhase.CHAPTERS,
        sequence=1,
        max_attempts=max_attempts,
    )
    session.commit()
    return step


def reread(session: Session, step_id: str) -> Step:
    """End this session's transaction and read the row the database actually holds."""
    session.rollback()
    session.expire_all()
    step = session.get(Step, step_id)
    assert step is not None
    return step


def test_only_one_of_two_connections_wins_a_contested_claim(
    worker_a: Session,
    worker_b: Session,
) -> None:
    step = seed_step(worker_a)
    # Read on B first, so B decides from a snapshot in which nobody holds the step.
    contender = worker_b.get(Step, step.id)
    assert contender is not None

    first = claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()
    second = claim_step(worker_b, contender, owner=OWNER_B, lease=LEASE, now=NOW)
    worker_b.commit()

    assert [first.outcome, second.outcome] == [
        ClaimOutcome.CLAIMED,
        ClaimOutcome.LEASED_ELSEWHERE,
    ]
    settled = reread(worker_a, step.id)
    assert settled.lease_owner == OWNER_A
    assert settled.attempt == 1


def test_a_crowd_of_workers_leaves_exactly_one_lease_holder(
    sessions: sessionmaker[Session],
    worker_a: Session,
) -> None:
    """Every delivery of one message arriving at once is the case the lease exists for."""
    step_id = seed_step(worker_a).id
    ready = threading.Barrier(CONTENDERS)

    def contend(index: int) -> ClaimOutcome:
        with sessions() as session:
            step = session.get(Step, step_id)
            assert step is not None
            ready.wait(timeout=30)
            claim = claim_step(
                session, step, owner=new_lease_owner(f"worker-{index}"), lease=LEASE, now=NOW
            )
            session.commit()
            return claim.outcome

    with ThreadPoolExecutor(max_workers=CONTENDERS) as pool:
        running = [pool.submit(contend, index) for index in range(CONTENDERS)]
        outcomes = [future.result() for future in running]

    assert outcomes.count(ClaimOutcome.CLAIMED) == 1
    assert set(outcomes) <= {ClaimOutcome.CLAIMED, ClaimOutcome.LEASED_ELSEWHERE}
    settled = reread(worker_a, step_id)
    assert settled.attempt == 1
    assert settled.status is StepStatus.RUNNING


def test_a_worker_whose_lease_was_taken_over_cannot_report_success(
    worker_a: Session,
    worker_b: Session,
) -> None:
    step = seed_step(worker_a)
    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()
    stolen_at = NOW + LEASE + timedelta(seconds=1)

    taken_over = worker_b.get(Step, step.id)
    assert taken_over is not None
    assert claim_step(worker_b, taken_over, owner=OWNER_B, lease=LEASE, now=stolen_at).claimed
    worker_b.commit()

    with pytest.raises(LeaseLost):
        complete_step(worker_a, step, owner=OWNER_A, now=stolen_at)

    settled = reread(worker_b, step.id)
    assert settled.status is StepStatus.RUNNING
    assert settled.lease_owner == OWNER_B


def test_a_heartbeat_from_a_worker_that_lost_the_lease_is_refused(
    worker_a: Session,
    worker_b: Session,
) -> None:
    step = seed_step(worker_a)
    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()
    stolen_at = NOW + LEASE + timedelta(seconds=1)

    taken_over = worker_b.get(Step, step.id)
    assert taken_over is not None
    claim_step(worker_b, taken_over, owner=OWNER_B, lease=LEASE, now=stolen_at)
    worker_b.commit()

    with pytest.raises(LeaseLost):
        heartbeat_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=stolen_at)


def test_an_uncommitted_heartbeat_extends_the_lease_for_nobody(
    worker_a: Session,
    worker_b: Session,
) -> None:
    """Why every heartbeat needs its own transaction rather than the working one."""
    step = seed_step(worker_a)
    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()
    beat_at = NOW + timedelta(minutes=4)

    heartbeat_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=beat_at)

    assert reread(worker_b, step.id).lease_expires_at == NOW + LEASE
    worker_a.commit()
    assert reread(worker_b, step.id).lease_expires_at == beat_at + LEASE


def test_an_uncommitted_claim_excludes_nobody(
    worker_a: Session,
    worker_b: Session,
) -> None:
    step = seed_step(worker_a)

    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)

    assert reread(worker_b, step.id).status is StepStatus.PENDING
    worker_a.commit()
    assert reread(worker_b, step.id).lease_owner == OWNER_A


def test_a_takeover_loses_to_a_heartbeat_that_landed_first(
    worker_a: Session,
    worker_b: Session,
) -> None:
    """A step on its final attempt whose worker is slow but demonstrably alive.

    The lease has expired, so a duplicate delivery decides the step is out of attempts and
    goes to mark it failed. Between that decision and the write, the worker heartbeats. The
    row must stay with the worker that is still doing the work.
    """
    step = seed_step(worker_a, max_attempts=1)
    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()
    expired_at = NOW + LEASE + timedelta(seconds=1)

    stale = worker_b.get(Step, step.id)
    assert stale is not None
    heartbeat_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=expired_at)
    worker_a.commit()

    claim = claim_step(worker_b, stale, owner=OWNER_B, lease=LEASE, now=expired_at)
    worker_b.commit()

    assert claim.outcome is ClaimOutcome.LEASED_ELSEWHERE
    settled = reread(worker_a, step.id)
    assert settled.status is StepStatus.RUNNING
    assert settled.lease_owner == OWNER_A
    assert settled.lease_expires_at == expired_at + LEASE


def test_a_step_nobody_is_running_still_gives_up_when_its_attempts_are_gone(
    worker_a: Session,
    worker_b: Session,
) -> None:
    """The guard must not turn "out of attempts" into a step that retries forever."""
    step = seed_step(worker_a, max_attempts=1)
    claim_step(worker_a, step, owner=OWNER_A, lease=LEASE, now=NOW)
    worker_a.commit()

    after_expiry = NOW + LEASE + timedelta(seconds=1)
    taking_over = worker_b.get(Step, step.id)
    assert taking_over is not None
    claim = claim_step(worker_b, taking_over, owner=OWNER_B, lease=LEASE, now=after_expiry)
    worker_b.commit()

    assert claim.outcome is ClaimOutcome.EXHAUSTED
    settled = reread(worker_a, step.id)
    assert settled.status is StepStatus.FAILED
    assert settled.error_code == "max_attempts_exhausted"
