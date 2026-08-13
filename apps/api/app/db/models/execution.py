from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.clock import utcnow
from app.db.base import TABLE_OPTIONS, Base, IdMixin, TimestampMixin
from app.db.models._columns import enum_column, id_fk, project_fk
from app.db.models.enums import ArtifactKind, RunPhase, RunStatus, StepStatus
from app.db.types import Money, Sha256, new_id

DEFAULT_PIPELINE_VERSION = "1"
DEFAULT_MAX_ATTEMPTS = 3

#: The order the generation graph walks through. A run may re-enter its current phase
#: (steps retry) and may move forward, but never backwards, with one exception below.
#:
#: Forward moves are deliberately not required to be adjacent. Phases are skippable by
#: design -- a tutorial with nothing to draw never enters ``diagrams``, and a source set
#: with no repository never enters ``analyze`` -- so an adjacency rule would refuse ordinary
#: runs. The consequence is that ``phase is publish`` proves only that the run reached the
#: end, not that any particular earlier phase ran; what a chapter is allowed to claim is
#: enforced by the validate step's own gate, not by this ordering.
PHASE_ORDER: Final[tuple[RunPhase, ...]] = (
    RunPhase.QUEUED,
    RunPhase.DISCOVER,
    RunPhase.SNAPSHOT,
    RunPhase.PARSE,
    RunPhase.INDEX,
    RunPhase.ANALYZE,
    RunPhase.OUTLINE,
    RunPhase.HUMAN_INTERRUPT,
    RunPhase.CHAPTERS,
    RunPhase.DIAGRAMS,
    RunPhase.VALIDATE,
    RunPhase.PUBLISH,
)

#: A reviewer who rejects the outline sends the run back to regenerate it.
BACKWARD_PHASE_EXCEPTIONS: Final[frozenset[tuple[RunPhase, RunPhase]]] = frozenset(
    {(RunPhase.HUMAN_INTERRUPT, RunPhase.OUTLINE)}
)

TERMINAL_RUN_STATUSES: Final[frozenset[RunStatus]] = frozenset(
    {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}
)

#: ``suspended`` means "waiting for a human"; ``phase`` says what it is waiting on.
ALLOWED_RUN_STATUS_TRANSITIONS: Final[dict[RunStatus, frozenset[RunStatus]]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SUSPENDED,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.SUSPENDED: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}

_PHASE_INDEX: Final = {phase: index for index, phase in enumerate(PHASE_ORDER)}

_UNCHECKED_RUN_STATE: ContextVar[bool] = ContextVar("unchecked_run_state", default=False)


class RunStateError(Exception):
    """Base class for refused run state changes."""


class IllegalRunStatus(RunStateError):
    """Raised when a status change is not in the documented transition table."""


class IllegalRunPhase(RunStateError):
    """Raised when a phase change would move the run backwards through the graph."""


def can_change_status(current: RunStatus, target: RunStatus) -> bool:
    return target in ALLOWED_RUN_STATUS_TRANSITIONS[current]


def assert_status_change(current: RunStatus, target: RunStatus) -> None:
    if not can_change_status(current, target):
        raise IllegalRunStatus(f"a {current.value} run cannot become {target.value}")


def can_advance_phase(current: RunPhase, target: RunPhase) -> bool:
    if (current, target) in BACKWARD_PHASE_EXCEPTIONS:
        return True
    return _PHASE_INDEX[target] >= _PHASE_INDEX[current]


def assert_phase_change(current: RunPhase, target: RunPhase) -> None:
    if not can_advance_phase(current, target):
        raise IllegalRunPhase(f"a run in {current.value} cannot go back to {target.value}")


@contextmanager
def unchecked_run_state() -> Iterator[None]:
    """Assign ``Run.status`` and ``Run.phase`` without the forward-only checks.

    The checks are attribute validators precisely so that no caller can bypass them by
    accident. This is the deliberate way through, for the two cases that need one: test
    fixtures building a state directly, and administrative repair of a run the pipeline
    left somewhere it cannot leave.
    """
    token = _UNCHECKED_RUN_STATE.set(True)
    try:
        yield
    finally:
        _UNCHECKED_RUN_STATE.reset(token)


class Run(IdMixin, TimestampMixin, Base):
    """One execution of the tutorial generation graph for a project.

    ``status`` says how the execution is doing and ``phase`` says where it got to; keeping
    them apart is what lets the UI show "suspended while waiting for outline approval"
    without inventing a status per stage. ``thread_id`` is the LangGraph thread this run
    resumes, and MySQL stays the authoritative record of both fields.

    Both fields are validated on assignment rather than only inside
    :mod:`app.runs.state_machine`, so a run cannot be walked backwards or revived from a
    terminal status by code that never called the state machine.
    :func:`unchecked_run_state` is the one way through.
    """

    __tablename__ = "runs"
    __table_args__ = (
        sa.Index("ix_runs_project_created_at", "project_id", "created_at"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    requested_by: Mapped[str | None] = id_fk(
        "users.id", ondelete="SET NULL", nullable=True, index=False
    )
    pipeline_version: Mapped[str] = mapped_column(
        sa.String(32), default=DEFAULT_PIPELINE_VERSION
    )
    status: Mapped[RunStatus] = enum_column(RunStatus, RunStatus.PENDING, index=True)
    phase: Mapped[RunPhase] = enum_column(RunPhase, RunPhase.QUEUED)
    thread_id: Mapped[str] = mapped_column(sa.String(64), unique=True, default=new_id)
    idempotency_key: Mapped[str | None] = mapped_column(
        sa.String(191), nullable=True, unique=True
    )
    input_hash: Mapped[str | None] = mapped_column(Sha256, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))

    @validates("status")
    def _refuse_an_undocumented_status_change(self, _key: str, value: Any) -> Any:
        current = _known_status(self.status)
        target = _known_status(value)
        if current is not None and target is not None and target is not current:
            assert_status_change(current, target)
        return value

    @validates("phase")
    def _refuse_a_backward_phase(self, _key: str, value: Any) -> Any:
        current = _known_phase(self.phase)
        target = _known_phase(value)
        if current is not None and target is not None:
            assert_phase_change(current, target)
        return value


def _known_status(value: Any) -> RunStatus | None:
    """Return the member ``value`` names, or nothing when the invariant does not apply.

    Nothing means one of two things: the attribute has never been set, so there is no
    transition yet, or the value is not in the vocabulary at all, in which case the column
    type produces the real error at flush time instead of this validator masking it.
    """
    if value is None or _UNCHECKED_RUN_STATE.get():
        return None
    try:
        return RunStatus(value)
    except ValueError:
        return None


def _known_phase(value: Any) -> RunPhase | None:
    if value is None or _UNCHECKED_RUN_STATE.get():
        return None
    try:
        return RunPhase(value)
    except ValueError:
        return None


class Step(IdMixin, TimestampMixin, Base):
    """One at-least-once unit of work inside a run.

    A worker claims a step by taking a time-boxed lease, does the side effect, writes its
    artifacts, and only then marks the step succeeded. ``idempotency_key`` is unique across
    the deployment, so a redelivered Celery message finds the existing row instead of
    duplicating work, and an expired lease lets another worker retry after a hard kill.
    """

    __tablename__ = "steps"
    __table_args__ = (
        sa.Index("ix_steps_run_sequence", "run_id", "sequence"),
        sa.Index("ix_steps_status_lease_expires_at", "status", "lease_expires_at"),
        TABLE_OPTIONS,
    )

    run_id: Mapped[str] = id_fk("runs.id", ondelete="CASCADE", index=False)
    project_id: Mapped[str] = project_fk()
    name: Mapped[str] = mapped_column(sa.String(64))
    phase: Mapped[RunPhase] = enum_column(RunPhase)
    status: Mapped[StepStatus] = enum_column(StepStatus, StepStatus.PENDING)
    sequence: Mapped[int] = mapped_column(sa.Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(sa.String(191), unique=True)
    attempt: Mapped[int] = mapped_column(sa.Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(sa.Integer, default=DEFAULT_MAX_ATTEMPTS)
    lease_owner: Mapped[str | None] = mapped_column(sa.String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(nullable=True)
    input_hash: Mapped[str | None] = mapped_column(Sha256, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(Sha256, nullable=True)
    model: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    tokens_in: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Money, default=Decimal("0"))
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_code: Mapped[str | None] = mapped_column(sa.String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(sa.Text, nullable=True)


class Artifact(IdMixin, Base):
    """Metadata for bytes held in content-addressed storage.

    The row records the digest, the relative storage path and the provenance; the payload
    itself never lands in MySQL. ``run_id`` and ``step_id`` become NULL rather than
    cascading, so deleting a run cannot erase the provenance of a published chapter.
    """

    __tablename__ = "artifacts"
    __table_args__ = (
        sa.UniqueConstraint("project_id", "kind", "sha256", name="uq_artifacts_project_kind_hash"),
        sa.Index("ix_artifacts_sha256", "sha256"),
        TABLE_OPTIONS,
    )

    project_id: Mapped[str] = project_fk(index=False)
    run_id: Mapped[str | None] = id_fk("runs.id", ondelete="SET NULL", nullable=True)
    step_id: Mapped[str | None] = id_fk("steps.id", ondelete="SET NULL", nullable=True)
    kind: Mapped[ArtifactKind] = enum_column(ArtifactKind)
    sha256: Mapped[str] = mapped_column(Sha256)
    storage_path: Mapped[str] = mapped_column(sa.String(512))
    media_type: Mapped[str] = mapped_column(sa.String(128), default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger, default=0)
    provenance: Mapped[dict[str, Any]] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
