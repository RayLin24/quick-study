"""The at-least-once execution contract for one unit of work.

Transaction boundaries
----------------------
Nothing in this module commits: every function issues statements and leaves the boundary
to its caller, because only the caller knows what else belongs in the same transaction.
Exactly three placements are correct, and getting one of them wrong silently breaks the
mutual exclusion the lease is supposed to provide:

* **The claim gets its own transaction, committed before the work starts.** An uncommitted
  claim is invisible to every other delivery, so two workers would both believe they hold
  the lease.
* **Every heartbeat gets its own transaction.** A lease extension that is not committed
  extends nothing, and the step is taken over while its worker is still running.
* **The artifacts a step produces and the ``complete_step`` that publishes them share one
  transaction.** ``succeeded`` is what tells a later delivery that the side effects already
  exist, so it must never become visible before they do.

:func:`run_step` places all three, and is the intended way to execute a step.

Lease owners
------------
``owner`` is the identity the mutual exclusion is built on: two concurrent executions that
present the *same* owner string re-enter the same lease instead of excluding each other, so
both would run the work. An owner therefore has to be unique per execution rather than per
host or per process -- ``celery --hostname=worker@%h`` gives every worker on a node the same
name, and a prefork pool multiplies it further. :func:`new_lease_owner` produces one.
"""

from __future__ import annotations

import hashlib
import os
import socket
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.clock import utcnow
from app.db.models import Run, Step
from app.db.models.enums import RunPhase, StepStatus
from app.db.models.execution import DEFAULT_MAX_ATTEMPTS

#: Matches the width of the unique ``steps.idempotency_key`` column.
IDEMPOTENCY_KEY_MAX_LENGTH: Final = 191

#: Matches the width of the ``steps.lease_owner`` column.
LEASE_OWNER_MAX_LENGTH: Final = 128

#: Matches the width of the ``steps.error_code`` column.
ERROR_CODE_MAX_LENGTH: Final = 64

#: Long enough for a model call, short enough that a killed worker is retried promptly.
DEFAULT_LEASE: Final = timedelta(minutes=5)

_SEPARATOR: Final = ":"
_ESCAPE: Final = "\\"


class StepError(Exception):
    """Base class for refused step operations."""


class LeaseLost(StepError):
    """Raised when a worker acts on a step it no longer holds the lease for.

    This is the guard that makes "at least once" safe: a worker that stalled past its
    lease, had the step taken over, and then woke up cannot report success or failure.
    """


class StepVanished(StepError):
    """Raised when the step row disappeared underneath a worker."""


class StepFailed(StepError):
    """Raise inside :func:`run_step` to record a diagnosis of your own choosing.

    Any other exception is recorded as a retryable failure named after its type; raise this
    with ``retryable=False`` for a problem that will not get better on the next attempt.
    """

    def __init__(self, error_code: str, error_message: str = "", *, retryable: bool = True):
        super().__init__(f"{error_code}: {error_message}" if error_message else error_code)
        self.error_code = error_code
        self.error_message = error_message
        self.retryable = retryable


class ClaimOutcome(StrEnum):
    """Why a claim attempt did or did not hand over the work."""

    #: The caller now holds the lease and must do the work.
    CLAIMED = "claimed"
    #: A previous delivery already finished it; the side effects exist.
    ALREADY_SUCCEEDED = "already_succeeded"
    #: Another worker holds a live lease; try again later.
    LEASED_ELSEWHERE = "leased_elsewhere"
    #: The step will not run again, whether it ran out of attempts or failed permanently.
    EXHAUSTED = "exhausted"
    #: The run was cancelled underneath this step.
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class StepClaim:
    """The result of trying to take over a step, together with its current row."""

    outcome: ClaimOutcome
    step: Step

    @property
    def claimed(self) -> bool:
        return self.outcome is ClaimOutcome.CLAIMED


def build_idempotency_key(*parts: str) -> str:
    """Build a deterministic key for one logical unit of work.

    The same inputs always produce the same key, so a redelivered message finds the
    existing row. Separators inside a part are escaped, because two different units of work
    that collide on one key make the second one look like it already succeeded. Over-long
    keys keep a readable prefix plus a digest of the whole thing, which stays both
    deterministic and unique inside the indexed column width.
    """
    escaped = [_escape_key_part(part.strip()) for part in parts]
    if not any(escaped):
        raise ValueError("an idempotency key needs at least one non-empty part")
    joined = _SEPARATOR.join(escaped)
    if len(joined) <= IDEMPOTENCY_KEY_MAX_LENGTH:
        return joined
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    prefix_length = IDEMPOTENCY_KEY_MAX_LENGTH - len(digest) - len(_SEPARATOR)
    return f"{joined[:prefix_length]}{_SEPARATOR}{digest}"


def new_lease_owner(label: str | None = None) -> str:
    """Return a lease owner that names exactly one execution of one step.

    The random suffix is what makes it unique; ``label`` only exists so an operator reading
    ``steps.lease_owner`` can tell which host and process is holding the row.
    """
    node = label if label is not None else f"{socket.gethostname()}#{os.getpid()}"
    token = uuid.uuid4().hex
    prefix = node[: LEASE_OWNER_MAX_LENGTH - len(token) - len(_SEPARATOR)]
    return f"{prefix}{_SEPARATOR}{token}"


def _escape_key_part(part: str) -> str:
    return part.replace(_ESCAPE, _ESCAPE * 2).replace(_SEPARATOR, f"{_ESCAPE}{_SEPARATOR}")


def ensure_step(
    session: Session,
    *,
    run: Run,
    name: str,
    phase: RunPhase,
    sequence: int = 0,
    idempotency_key: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    input_hash: str | None = None,
) -> Step:
    """Return the step for this unit of work, creating it only the first time.

    Two workers handling the same redelivered message end up with the same row: the
    loser of the insert race reads what the winner wrote.
    """
    key = idempotency_key or build_idempotency_key(run.id, phase.value, name, str(sequence))
    existing = _find_by_key(session, key)
    if existing is not None:
        return existing

    step = Step(
        run_id=run.id,
        project_id=run.project_id,
        name=name,
        phase=phase,
        sequence=sequence,
        idempotency_key=key,
        max_attempts=max_attempts,
        input_hash=input_hash,
    )
    savepoint = session.begin_nested()
    try:
        session.add(step)
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        raced = _find_by_key(session, key)
        if raced is None:
            raise
        return raced
    savepoint.commit()
    return step


def claim_step(
    session: Session,
    step: Step,
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    now: datetime | None = None,
) -> StepClaim:
    """Take a time-boxed lease on ``step`` so exactly one worker executes it.

    ``owner`` must name this execution alone; see :func:`new_lease_owner`. Re-entering while
    still holding the lease extends it without spending an attempt; an expired lease may be
    taken over by anyone, which is how a hard-killed worker's step gets retried.

    Commit before starting the work: an uncommitted claim excludes nobody.
    """
    moment = now or utcnow()
    current = _reload(session, step)

    if current.status is StepStatus.SUCCEEDED:
        return StepClaim(ClaimOutcome.ALREADY_SUCCEEDED, current)
    if current.status is StepStatus.CANCELLED:
        return StepClaim(ClaimOutcome.CANCELLED, current)
    if current.status is StepStatus.FAILED:
        return StepClaim(ClaimOutcome.EXHAUSTED, current)

    holds_lease = current.status is StepStatus.RUNNING and current.lease_owner == owner
    lease_expired = current.lease_expires_at is None or current.lease_expires_at <= moment
    if current.status is StepStatus.RUNNING and not holds_lease and not lease_expired:
        return StepClaim(ClaimOutcome.LEASED_ELSEWHERE, current)

    next_attempt = current.attempt if holds_lease else current.attempt + 1
    if next_attempt > current.max_attempts:
        if not _exhaust(session, current, moment):
            return StepClaim(ClaimOutcome.LEASED_ELSEWHERE, _reload(session, step))
        return StepClaim(ClaimOutcome.EXHAUSTED, current)

    updated = _guarded_update(
        session,
        current,
        guard=_unchanged_since_read(current),
        values={
            "status": StepStatus.RUNNING,
            "attempt": next_attempt,
            "lease_owner": owner,
            "lease_expires_at": moment + lease,
            "started_at": current.started_at or moment,
            "error_code": None,
            "error_message": None,
        },
    )
    if not updated:
        return StepClaim(ClaimOutcome.LEASED_ELSEWHERE, _reload(session, step))
    return StepClaim(ClaimOutcome.CLAIMED, _reload(session, step))


def heartbeat_step(
    session: Session,
    step: Step,
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    now: datetime | None = None,
) -> None:
    """Extend the lease of work that is still running, without spending an attempt.

    Commit it on its own: an extension nobody else can see does not stop a takeover.
    """
    moment = now or utcnow()
    if not _guarded_update(
        session,
        step,
        guard=_lease_holder_guard(owner),
        values={"lease_expires_at": moment + lease},
    ):
        raise LeaseLost(_lease_lost_message(session, step, owner))
    _reload(session, step)


def complete_step(
    session: Session,
    step: Step,
    *,
    owner: str,
    model: str | None = None,
    prompt_hash: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost_usd: Decimal = Decimal("0"),
    now: datetime | None = None,
) -> None:
    """Mark the step succeeded in one atomic statement.

    Write the artifacts in this same transaction and commit both together: the row flipping
    to ``succeeded`` is what tells later deliveries the side effects already happened, so it
    must not become visible before them.

    ``model`` and ``prompt_hash`` are left alone when they are not supplied, because the
    attempt may already have recorded them; the usage counters describe this attempt and are
    written as given.
    """
    moment = now or utcnow()
    values: dict[str, Any] = {
        "status": StepStatus.SUCCEEDED,
        "finished_at": moment,
        "lease_owner": None,
        "lease_expires_at": None,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cost_usd": cost_usd,
        "error_code": None,
        "error_message": None,
    }
    if model is not None:
        values["model"] = model
    if prompt_hash is not None:
        values["prompt_hash"] = prompt_hash
    if not _guarded_update(session, step, guard=_lease_holder_guard(owner), values=values):
        raise LeaseLost(_lease_lost_message(session, step, owner))
    _reload(session, step)


def fail_step(
    session: Session,
    step: Step,
    *,
    owner: str,
    error_code: str,
    error_message: str,
    retryable: bool = True,
    now: datetime | None = None,
) -> StepStatus:
    """Record a failure and return whether the step will be retried.

    A retryable failure with attempts left goes back to ``pending`` so the next delivery
    picks it up; anything else is final and keeps the diagnosis on the row.
    """
    moment = now or utcnow()
    current = _reload(session, step)
    retry = retryable and current.attempt < current.max_attempts
    target = StepStatus.PENDING if retry else StepStatus.FAILED

    if not _guarded_update(
        session,
        current,
        guard=_lease_holder_guard(owner),
        values={
            "status": target,
            "lease_owner": None,
            "lease_expires_at": None,
            "error_code": error_code,
            "error_message": error_message,
            "finished_at": None if retry else moment,
        },
    ):
        raise LeaseLost(_lease_lost_message(session, step, owner))
    _reload(session, step)
    return target


@dataclass(slots=True)
class StepExecution:
    """The handle a worker holds while it owns the lease on a step."""

    session: Session
    step: Step
    owner: str
    lease: timedelta
    model: str | None = None
    prompt_hash: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = field(default_factory=lambda: Decimal("0"))

    def record_usage(
        self,
        *,
        model: str | None = None,
        prompt_hash: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        cost_usd: Decimal = Decimal("0"),
    ) -> None:
        """Accumulate what this attempt spent; :func:`run_step` writes it on success."""
        self.model = model or self.model
        self.prompt_hash = prompt_hash or self.prompt_hash
        self.tokens_in += tokens_in
        self.tokens_out += tokens_out
        self.cost_usd += cost_usd

    def heartbeat(self, *, now: datetime | None = None) -> None:
        """Extend the lease in a transaction of its own.

        A separate session is the point: committing the working session here would publish
        half-written artifacts, and not committing at all would extend nothing.
        """
        with Session(bind=self.session.get_bind(), expire_on_commit=False) as beat:
            current = beat.get(Step, self.step.id)
            if current is None:
                raise StepVanished(self.step.id)
            heartbeat_step(beat, current, owner=self.owner, lease=self.lease, now=now)
            beat.commit()
        self.session.expire(self.step)


@contextmanager
def run_step(
    session: Session,
    step: Step,
    *,
    owner: str,
    lease: timedelta = DEFAULT_LEASE,
    now: datetime | None = None,
) -> Iterator[StepExecution | None]:
    """Execute one step with the transaction boundaries this module requires.

    Yields the execution handle when the caller won the lease and must do the work, or
    ``None`` when somebody else already has it or already finished it -- check before doing
    anything with side effects. Everything the body writes through ``session`` is committed
    together with the success, and discarded together with a failure.
    """
    claim = claim_step(session, step, owner=owner, lease=lease, now=now)
    session.commit()
    if not claim.claimed:
        yield None
        return

    execution = StepExecution(session=session, step=claim.step, owner=owner, lease=lease)
    try:
        yield execution
    except Exception as error:
        session.rollback()
        _record_failure(execution, error)
        raise
    complete_step(
        session,
        execution.step,
        owner=owner,
        model=execution.model,
        prompt_hash=execution.prompt_hash,
        tokens_in=execution.tokens_in,
        tokens_out=execution.tokens_out,
        cost_usd=execution.cost_usd,
    )
    session.commit()


def _record_failure(execution: StepExecution, error: Exception) -> None:
    """Write the diagnosis of a failed attempt, unless the lease is no longer ours."""
    if isinstance(error, StepFailed):
        code, message, retryable = error.error_code, error.error_message, error.retryable
    else:
        code, message, retryable = type(error).__name__, str(error), True
    try:
        fail_step(
            execution.session,
            execution.step,
            owner=execution.owner,
            error_code=code[:ERROR_CODE_MAX_LENGTH],
            error_message=message,
            retryable=retryable,
        )
    except StepError:
        execution.session.rollback()
        return
    execution.session.commit()


def _find_by_key(session: Session, key: str) -> Step | None:
    return session.scalars(sa.select(Step).where(Step.idempotency_key == key)).one_or_none()


def _reload(session: Session, step: Step) -> Step:
    """Re-read the row so decisions are made on what the database currently holds."""
    session.flush()
    session.expire(step)
    current = session.get(Step, step.id)
    if current is None:
        raise StepVanished(step.id)
    return current


def _matches(column: Any, value: Any) -> Any:
    return column.is_(None) if value is None else column == value


def _lease_holder_guard(owner: str) -> list[Any]:
    return [Step.status == StepStatus.RUNNING, Step.lease_owner == owner]


def _unchanged_since_read(step: Step) -> list[Any]:
    """Match only the row that was read, so anything that moved underneath wins the race.

    ``lease_expires_at`` belongs in the guard as much as the status does: a heartbeat that
    lands between the read and the write changes nothing else, and without it a takeover
    would still be applied to work that is demonstrably alive.
    """
    return [
        Step.status == step.status,
        Step.attempt == step.attempt,
        _matches(Step.lease_owner, step.lease_owner),
        _matches(Step.lease_expires_at, step.lease_expires_at),
    ]


def _guarded_update(
    session: Session,
    step: Step,
    *,
    guard: list[Any],
    values: dict[str, Any],
) -> bool:
    """Apply ``values`` only if ``guard`` still holds, reporting whether it did."""
    session.flush()
    result = session.execute(
        sa.update(Step).where(Step.id == step.id, *guard).values(**values)
    )
    return result.rowcount == 1


def _exhaust(session: Session, step: Step, now: datetime) -> bool:
    """Give up on a step that has no attempts left, reporting whether it was still ours.

    The guard matters: the row may be running its final attempt with an expired lease while
    the worker is alive and heartbeating, and marking that failed would throw away real work
    and hand the lease to nobody.
    """
    exhausted = _guarded_update(
        session,
        step,
        guard=_unchanged_since_read(step),
        values={
            "status": StepStatus.FAILED,
            "lease_owner": None,
            "lease_expires_at": None,
            "finished_at": now,
            "error_code": "max_attempts_exhausted",
            "error_message": f"gave up after {step.max_attempts} attempts",
        },
    )
    _reload(session, step)
    return exhausted


def _lease_lost_message(session: Session, step: Step, owner: str) -> str:
    current = session.get(Step, step.id)
    if current is None:
        return f"step {step.id} no longer exists"
    return (
        f"{owner} does not hold the lease on step {current.id}: "
        f"status={current.status.value} owner={current.lease_owner}"
    )
