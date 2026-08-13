"""Recording what each graph node did, and stopping it from doing it twice.

A node is a step in the sense ``app.runs.steps`` already defines: it is delivered at least
once, it is identified by an idempotency key, and a worker executes it only while holding a
lease. This module is the seam between the graph and that contract, so the graph can be
exercised without a database and the database stays the authoritative record of attempts,
tokens, cost and errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import Run, Step
from app.db.models.enums import RunPhase
from app.runs.state_machine import advance_phase, record_run_usage
from app.runs.steps import (
    DEFAULT_LEASE,
    ClaimOutcome,
    claim_step,
    complete_step,
    ensure_step,
    fail_step,
)

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True, slots=True)
class NodeCall:
    """The identity of one execution of one node."""

    node: str
    phase: RunPhase
    run_id: str
    project_id: str
    pipeline_version: str
    input_hash: str
    idempotency_key: str
    attempt: int


@dataclass(frozen=True, slots=True)
class NodeOutcome:
    """What a node produced, plus what it cost to produce it.

    ``update`` is merged into the graph state through the reducers; everything else is
    provenance the run has to keep whether or not the node called a model.
    """

    update: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    prompt_hash: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class NodeFailure:
    node: str
    attempt: int
    error_code: str
    error_message: str


@dataclass(frozen=True, slots=True)
class ClaimedNode:
    outcome: ClaimOutcome
    call: NodeCall

    @property
    def claimed(self) -> bool:
        return self.outcome is ClaimOutcome.CLAIMED


class StepRecorder(ABC):
    """Decides whether a node may run, and records what happened when it did."""

    @abstractmethod
    def claim(
        self,
        *,
        node: str,
        phase: RunPhase,
        run_id: str,
        project_id: str,
        pipeline_version: str,
        input_hash: str,
        idempotency_key: str,
    ) -> ClaimedNode:
        """Take the lease on this node, or report why the caller may not run it."""

    @abstractmethod
    def succeeded(self, call: NodeCall, outcome: NodeOutcome) -> None:
        """Record a finished node. Called only after its side effects are durable."""

    @abstractmethod
    def failed(self, call: NodeCall, error: BaseException) -> NodeFailure:
        """Record why a node did not finish."""


class InMemoryStepRecorder(StepRecorder):
    """Keeps the record in the process. For tests and for running without a database."""

    def __init__(self, outcomes: dict[str, ClaimOutcome] | None = None) -> None:
        self.calls: list[NodeCall] = []
        self.completions: list[tuple[NodeCall, NodeOutcome]] = []
        self._failures: list[NodeFailure] = []
        self._outcomes = outcomes or {}
        self._attempts: dict[str, int] = {}

    def claim(
        self,
        *,
        node: str,
        phase: RunPhase,
        run_id: str,
        project_id: str,
        pipeline_version: str,
        input_hash: str,
        idempotency_key: str,
    ) -> ClaimedNode:
        self._attempts[idempotency_key] = self._attempts.get(idempotency_key, 0) + 1
        call = NodeCall(
            node=node,
            phase=phase,
            run_id=run_id,
            project_id=project_id,
            pipeline_version=pipeline_version,
            input_hash=input_hash,
            idempotency_key=idempotency_key,
            attempt=self._attempts[idempotency_key],
        )
        self.calls.append(call)
        return ClaimedNode(self._outcomes.get(node, ClaimOutcome.CLAIMED), call)

    def succeeded(self, call: NodeCall, outcome: NodeOutcome) -> None:
        self.completions.append((call, outcome))

    def failed(self, call: NodeCall, error: BaseException) -> NodeFailure:
        failure = NodeFailure(
            node=call.node,
            attempt=call.attempt,
            error_code=type(error).__name__,
            error_message=str(error),
        )
        self._failures.append(failure)
        return failure

    def completed_nodes(self) -> list[str]:
        return [call.node for call, _ in self.completions]

    def failures(self) -> list[NodeFailure]:
        return list(self._failures)


class DatabaseStepRecorder(StepRecorder):
    """Writes every node execution to the ``steps`` table.

    Each call runs in its own short transaction so a checkpoint and its step row are never
    waiting on each other, and so a crash between two nodes leaves the finished one marked
    succeeded. The run's phase and usage totals are advanced here too: the graph state is a
    working copy, the row is what the API and the reviewer read.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        owner: str,
        lease: timedelta = DEFAULT_LEASE,
    ) -> None:
        self._session_factory = session_factory
        self._owner = owner
        self._lease = lease

    def claim(
        self,
        *,
        node: str,
        phase: RunPhase,
        run_id: str,
        project_id: str,
        pipeline_version: str,
        input_hash: str,
        idempotency_key: str,
    ) -> ClaimedNode:
        with self._session_factory() as session:
            run = _require_run(session, run_id)
            step = ensure_step(
                session,
                run=run,
                name=node,
                phase=phase,
                idempotency_key=idempotency_key,
                input_hash=input_hash,
            )
            claim = claim_step(session, step, owner=self._owner, lease=self._lease)
            call = NodeCall(
                node=node,
                phase=phase,
                run_id=run_id,
                project_id=project_id,
                pipeline_version=pipeline_version,
                input_hash=input_hash,
                idempotency_key=idempotency_key,
                attempt=claim.step.attempt,
            )
            return ClaimedNode(claim.outcome, call)

    def succeeded(self, call: NodeCall, outcome: NodeOutcome) -> None:
        with self._session_factory() as session:
            run = _require_run(session, call.run_id)
            step = _require_step(session, call.idempotency_key)
            complete_step(
                session,
                step,
                owner=self._owner,
                model=outcome.model,
                prompt_hash=outcome.prompt_hash,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                cost_usd=outcome.cost_usd,
            )
            record_run_usage(
                run,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                cost_usd=outcome.cost_usd,
            )
            advance_phase(run, call.phase)

    def failed(self, call: NodeCall, error: BaseException) -> NodeFailure:
        failure = NodeFailure(
            node=call.node,
            attempt=call.attempt,
            error_code=type(error).__name__,
            error_message=str(error),
        )
        with self._session_factory() as session:
            step = _require_step(session, call.idempotency_key)
            fail_step(
                session,
                step,
                owner=self._owner,
                error_code=failure.error_code,
                error_message=failure.error_message,
            )
        return failure


def _require_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"run {run_id} no longer exists")
    return run


def _require_step(session: Session, idempotency_key: str) -> Step:
    step = session.scalars(
        sa.select(Step).where(Step.idempotency_key == idempotency_key)
    ).one_or_none()
    if step is None:
        raise LookupError(f"no step for idempotency key {idempotency_key}")
    return step
