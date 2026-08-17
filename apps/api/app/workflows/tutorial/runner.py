"""Waking the graph up, and keeping MySQL the authoritative record of what happened.

Celery delivers "this run needs attention". Everything else is read from and written to
the database: which run it is, what phase it reached, whether it is waiting for a reviewer,
what each node cost and why it failed. A lost message therefore costs latency and a
duplicated one costs nothing, because both are resolved against rows rather than against
the message.

Each wake-up is itself a step with its own idempotency key, so redelivering the same
wake-up is refused by the lease while a genuinely new one — the reviewer's decision, or a
retry after a crash — gets its own row.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any, Final

import sqlalchemy as sa
from langgraph.types import Command
from sqlalchemy.orm import Session

from app.db.models import Project, Run, Source, Step
from app.db.models.enums import RunPhase, RunStatus
from app.runs.state_machine import (
    fail_run,
    resume_run,
    start_run,
    succeed_run,
    suspend_run,
)
from app.runs.steps import (
    ClaimOutcome,
    build_idempotency_key,
    claim_step,
    complete_step,
    ensure_step,
    fail_step,
)
from app.storage.content_store import ContentAddressedStore
from app.workflows.checkpointing import CheckpointerProvider
from app.workflows.publication import persist_export_bundle, persist_pending_outline
from app.workflows.tutorial.graph import compile_tutorial_graph
from app.workflows.tutorial.nodes import TutorialNodes
from app.workflows.tutorial.recording import DatabaseStepRecorder, StepRecorder
from app.workflows.tutorial.state import (
    PIPELINE_VERSION,
    input_digest,
    new_tutorial_state,
    tutorial_request,
)

SessionFactory = Callable[[], AbstractContextManager[Session]]

#: The name of the step that represents one delivery of "look at this run".
WAKE_STEP: Final = "wake"

#: Checkpoints are written before each node, so a node that has to be redone is redone
#: whole; ``sync`` is what makes a hard-killed worker lose at most one node of progress.
DURABILITY: Final = "sync"


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """What one wake-up achieved, in terms a queue message can carry."""

    run_id: str
    status: RunStatus
    phase: RunPhase
    interrupt: dict[str, Any] | None = None
    skipped: bool = False


class TutorialRunner:
    """Drives one run of the generation graph on behalf of a Celery worker."""

    def __init__(
        self,
        *,
        provider: CheckpointerProvider,
        session_factory: SessionFactory,
        owner: str,
        nodes: TutorialNodes | None = None,
        pipeline_version: str = PIPELINE_VERSION,
        store: ContentAddressedStore | None = None,
    ) -> None:
        self._provider = provider
        self._session_factory = session_factory
        self._owner = owner
        self._nodes = nodes or TutorialNodes()
        self._pipeline_version = pipeline_version
        self._store = store

    def start(self, run_id: str) -> RunOutcome:
        """Begin, or take over, the graph for ``run_id``."""
        return self._wake(run_id, reason="start", resume=None)

    def resume(self, run_id: str, decision: Mapping[str, Any]) -> RunOutcome:
        """Deliver a reviewer's decision to a suspended run.

        The wake-up key includes the decision, so replaying the same approval is a
        redelivery and is ignored, while a different decision is new work.
        """
        reason = f"resume:{input_digest(dict(decision))[:32]}"
        return self._wake(run_id, reason=reason, resume=dict(decision))

    def _wake(
        self, run_id: str, *, reason: str, resume: dict[str, Any] | None
    ) -> RunOutcome:
        wake_key = build_idempotency_key(run_id, WAKE_STEP, reason)
        with self._session_factory() as session:
            run = _require_run(session, run_id)
            if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
                return RunOutcome(run_id, run.status, run.phase, skipped=True)
            step = ensure_step(
                session, run=run, name=WAKE_STEP, phase=run.phase, idempotency_key=wake_key
            )
            claim = claim_step(session, step, owner=self._owner)
            if claim.outcome is not ClaimOutcome.CLAIMED:
                return RunOutcome(run_id, run.status, run.phase, skipped=True)
            if run.status is RunStatus.PENDING:
                start_run(run)
            elif run.status is RunStatus.SUSPENDED:
                resume_run(run)
            initial = None if resume is not None else self._initial_state(session, run)
            thread_id = run.thread_id

        try:
            result = self._invoke(thread_id, initial, resume)
        except Exception as error:
            return self._record_failure(run_id, wake_key, error)
        return self._record_result(run_id, wake_key, result)

    def _invoke(
        self,
        thread_id: str,
        initial: dict[str, Any] | None,
        resume: dict[str, Any] | None,
    ) -> dict[str, Any]:
        recorder: StepRecorder = DatabaseStepRecorder(
            session_factory=self._session_factory, owner=self._owner
        )
        with self._provider.checkpointer() as checkpointer:
            graph = compile_tutorial_graph(
                checkpointer=checkpointer,
                nodes=self._nodes,
                recorder=recorder,
                pipeline_version=self._pipeline_version,
            )
            payload: Any = Command(resume=resume) if resume is not None else initial
            return graph.invoke(
                payload,
                {"configurable": {"thread_id": thread_id}},
                durability=DURABILITY,
            )

    def _record_result(
        self, run_id: str, wake_key: str, result: Mapping[str, Any]
    ) -> RunOutcome:
        interrupts = result.get("__interrupt__") or ()
        pending = dict(interrupts[0].value) if interrupts else None
        with self._session_factory() as session:
            run = _require_run(session, run_id)
            if pending is not None:
                suspend_run(run, phase=RunPhase.HUMAN_INTERRUPT)
                outline = persist_pending_outline(session, run, pending)
                pending = {**pending, "outline_id": outline.id}
            elif run.phase is RunPhase.PUBLISH:
                succeed_run(run)
                if self._store is not None:
                    persist_export_bundle(session, self._store, run, result)
            complete_step(session, _wake_step(session, wake_key), owner=self._owner)
            return RunOutcome(run_id, run.status, run.phase, interrupt=pending)

    def _record_failure(self, run_id: str, wake_key: str, error: Exception) -> RunOutcome:
        with self._session_factory() as session:
            run = _require_run(session, run_id)
            fail_run(run, error_code=type(error).__name__, error_message=str(error))
            fail_step(
                session,
                _wake_step(session, wake_key),
                owner=self._owner,
                error_code=type(error).__name__,
                error_message=str(error),
                retryable=False,
            )
            return RunOutcome(run_id, run.status, run.phase)

    def _initial_state(self, session: Session, run: Run) -> dict[str, Any]:
        """Build the graph's starting state from what MySQL already knows about the run.

        The code languages analysed in depth are decided by the analysis phase from the
        files it actually finds, so the request starts without them.
        """
        project = session.get(Project, run.project_id)
        if project is None:
            raise LookupError(f"run {run.id} points at a project that no longer exists")
        sources = session.scalars(
            sa.select(Source).where(Source.project_id == run.project_id).order_by(Source.id)
        ).all()
        return new_tutorial_state(
            run_id=run.id,
            project_id=run.project_id,
            thread_id=run.thread_id,
            request=tutorial_request(
                title=project.name,
                output_language=project.output_language,
                reader_level=project.reader_level.value,
                length_preset=project.length_preset.value,
                sources=[
                    {"kind": source.kind.value, "locator": source.locator}
                    for source in sources
                ],
            ),
        )


def _require_run(session: Session, run_id: str) -> Run:
    run = session.get(Run, run_id)
    if run is None:
        raise LookupError(f"run {run_id} does not exist")
    return run


def _wake_step(session: Session, idempotency_key: str) -> Step:
    step = session.scalars(
        sa.select(Step).where(Step.idempotency_key == idempotency_key)
    ).one_or_none()
    if step is None:
        raise LookupError(f"the wake-up step {idempotency_key} disappeared")
    return step
