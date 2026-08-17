"""Waking the graph up from Celery, with MySQL as the authoritative record.

The runner is the seam between the queue and the graph. Celery only says "this run needs
attention"; every fact about what happened — status, phase, attempts, tokens, cost and
errors — is read from and written to the database, so a lost or duplicated message cannot
change the story. These tests run against the SQLite fixture database and the in-memory
checkpointer.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import pytest
import sqlalchemy as sa
from conftest import alembic_config, make_project, make_run, reset_mysql_database
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from test_checkpointer_contract import checkpointer_url, recreate_schema, render

from alembic import command
from app.db.models import Outline, Run, Step
from app.db.models.enums import (
    ApprovalDecision,
    OutlineStatus,
    RunPhase,
    RunStatus,
    StepStatus,
)
from app.workflows.checkpointing import (
    InMemoryCheckpointerProvider,
    MySQLCheckpointerProvider,
)
from app.workflows.tutorial import NodeOutcome, TutorialNodes
from app.workflows.tutorial.runner import TutorialRunner

APPROVE = {"decision": ApprovalDecision.APPROVED.value, "note": ""}
REJECT = {"decision": ApprovalDecision.REJECTED.value, "note": "thin"}

SessionFactory = Callable[[], AbstractContextManager[Session]]


@pytest.fixture
def session_factory(engine: Engine) -> SessionFactory:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    @contextmanager
    def scope() -> Iterator[Session]:
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    return scope


@pytest.fixture
def run_id(db: Session) -> str:
    project = make_project(db)
    run = make_run(db, project=project)
    db.commit()
    return run.id


@pytest.fixture
def runner(session_factory: SessionFactory) -> TutorialRunner:
    provider = InMemoryCheckpointerProvider()
    provider.ensure_schema()
    return TutorialRunner(provider=provider, session_factory=session_factory, owner="worker-1")


def load_run(session_factory: SessionFactory, run_id: str) -> Run:
    with session_factory() as session:
        return session.get(Run, run_id)


def steps_of(session_factory: SessionFactory, run_id: str) -> list[Step]:
    with session_factory() as session:
        return list(
            session.scalars(sa.select(Step).where(Step.run_id == run_id).order_by(Step.created_at))
        )


class TestStarting:
    def test_a_started_run_suspends_waiting_for_the_outline_approval(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        outcome = runner.start(run_id)

        assert outcome.status is RunStatus.SUSPENDED
        assert outcome.phase is RunPhase.HUMAN_INTERRUPT
        assert outcome.interrupt is not None
        assert outcome.interrupt["kind"] == "outline_approval"

        run = load_run(session_factory, run_id)
        assert run.status is RunStatus.SUSPENDED
        assert run.phase is RunPhase.HUMAN_INTERRUPT
        assert run.started_at is not None

        with session_factory() as session:
            outline = session.scalars(
                sa.select(Outline).where(Outline.run_id == run_id)
            ).one()
        assert outline.status is OutlineStatus.PENDING_APPROVAL
        assert outline.structure["chapters"]

    def test_every_node_leaves_a_step_row_behind(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)

        steps = {step.name: step for step in steps_of(session_factory, run_id)}
        for name in ("discover", "snapshot", "parse", "index", "analyze", "outline"):
            assert steps[name].status is StepStatus.SUCCEEDED
            assert steps[name].attempt == 1
            assert steps[name].input_hash is not None
        assert "chapters" not in steps

    def test_the_wake_up_itself_is_recorded_as_a_step(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        wake = next(step for step in steps_of(session_factory, run_id) if step.name == "wake")
        assert wake.status is StepStatus.SUCCEEDED
        assert wake.idempotency_key == f"{run_id}:wake:start"


class TestDuplicateDelivery:
    def test_a_redelivered_start_does_not_run_the_graph_again(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        before = len(steps_of(session_factory, run_id))

        outcome = runner.start(run_id)

        assert outcome.skipped is True
        assert len(steps_of(session_factory, run_id)) == before

    def test_a_redelivered_approval_does_not_regenerate_the_tutorial(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        runner.resume(run_id, APPROVE)
        before = len(steps_of(session_factory, run_id))

        outcome = runner.resume(run_id, APPROVE)

        assert outcome.skipped is True
        assert len(steps_of(session_factory, run_id)) == before

    def test_a_different_decision_is_a_different_wake_up(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        rejected = runner.resume(run_id, REJECT)
        assert rejected.skipped is False
        assert rejected.phase is RunPhase.HUMAN_INTERRUPT

        approved = runner.resume(run_id, APPROVE)
        assert approved.skipped is False
        assert approved.status is RunStatus.SUCCEEDED


class TestFinishing:
    def test_an_approved_outline_takes_the_run_all_the_way_to_published(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        outcome = runner.resume(run_id, APPROVE)

        assert outcome.status is RunStatus.SUCCEEDED
        assert outcome.phase is RunPhase.PUBLISH

        run = load_run(session_factory, run_id)
        assert run.status is RunStatus.SUCCEEDED
        assert run.phase is RunPhase.PUBLISH
        assert run.finished_at is not None

    def test_model_usage_is_accumulated_on_the_run(
        self, session_factory: SessionFactory, run_id: str
    ) -> None:
        def outline(state: dict[str, Any], call: Any) -> NodeOutcome:
            return NodeOutcome(
                update={
                    "outline": {
                        "version": 1,
                        "title": "t",
                        "summary": "",
                        "chapters": [{"slug": "a", "title": "A", "ordinal": 0, "summary": ""}],
                    }
                },
                model="deepseek-chat",
                tokens_in=100,
                tokens_out=200,
            )

        provider = InMemoryCheckpointerProvider()
        provider.ensure_schema()
        runner = TutorialRunner(
            provider=provider,
            session_factory=session_factory,
            owner="worker-1",
            nodes=TutorialNodes(outline=outline),
        )
        runner.start(run_id)

        run = load_run(session_factory, run_id)
        assert run.tokens_in == 100
        assert run.tokens_out == 200

    def test_a_rejected_outline_sends_the_run_back_and_keeps_it_suspended(
        self, runner: TutorialRunner, session_factory: SessionFactory, run_id: str
    ) -> None:
        runner.start(run_id)
        outcome = runner.resume(run_id, REJECT)

        assert outcome.status is RunStatus.SUSPENDED
        assert outcome.phase is RunPhase.HUMAN_INTERRUPT
        run = load_run(session_factory, run_id)
        assert run.status is RunStatus.SUSPENDED


class TestFailure:
    def test_a_failing_node_fails_the_run_and_the_step(
        self, session_factory: SessionFactory, run_id: str
    ) -> None:
        def analyze(state: dict[str, Any], call: Any) -> NodeOutcome:
            raise RuntimeError("the analyser fell over")

        provider = InMemoryCheckpointerProvider()
        provider.ensure_schema()
        runner = TutorialRunner(
            provider=provider,
            session_factory=session_factory,
            owner="worker-1",
            nodes=TutorialNodes(analyze=analyze),
        )

        outcome = runner.start(run_id)

        assert outcome.status is RunStatus.FAILED
        run = load_run(session_factory, run_id)
        assert run.status is RunStatus.FAILED
        assert run.error_code == "RuntimeError"
        assert "the analyser fell over" in run.error_message

        analyse_step = next(
            step for step in steps_of(session_factory, run_id) if step.name == "analyze"
        )
        assert analyse_step.error_code == "RuntimeError"


class TestCeleryOnlyWakesTheRunUp:
    def test_the_task_carries_no_business_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.workflows import tasks

        seen: dict[str, Any] = {}

        class FakeRunner:
            def start(self, run_id: str) -> Any:
                seen["run_id"] = run_id
                return _outcome(run_id)

            def resume(self, run_id: str, decision: dict[str, Any]) -> Any:
                seen["run_id"] = run_id
                seen["decision"] = decision
                return _outcome(run_id)

        def _outcome(run_id: str) -> Any:
            from app.workflows.tutorial.runner import RunOutcome

            return RunOutcome(
                run_id=run_id,
                status=RunStatus.SUSPENDED,
                phase=RunPhase.HUMAN_INTERRUPT,
                interrupt={"kind": "outline_approval", "outline": {"chapters": []}},
                skipped=False,
            )

        monkeypatch.setattr(tasks, "build_runner", lambda: FakeRunner())

        started = tasks.start_tutorial_run(run_id="run-9")
        assert started == {
            "run_id": "run-9",
            "status": RunStatus.SUSPENDED.value,
            "phase": RunPhase.HUMAN_INTERRUPT.value,
            "awaiting": "outline_approval",
            "skipped": False,
        }

        resumed = tasks.resume_tutorial_run(run_id="run-9", decision=APPROVE)
        assert resumed["run_id"] == "run-9"
        assert seen["decision"] == APPROVE

    def test_both_tasks_are_registered_with_celery(self) -> None:
        from app.worker import celery_app

        assert "app.workflows.start_tutorial_run" in celery_app.tasks
        assert "app.workflows.resume_tutorial_run" in celery_app.tasks


class TestOnRealMySQL:
    """The two stores together: domain rows in MySQL, checkpoints in the MySQL adapter."""

    @pytest.fixture
    def domain_engine(self, mysql_url: str) -> Iterator[Engine]:
        """A migrated domain schema of this phase's own, next to the checkpoint one."""
        target = sa.make_url(mysql_url)
        url = render(target.set(database=f"{target.database}_workflow"))
        command.upgrade(alembic_config(reset_mysql_database(url)), "head")
        engine = sa.create_engine(url, future=True)
        try:
            yield engine
        finally:
            engine.dispose()

    @pytest.fixture
    def mysql_runner(
        self, domain_engine: Engine
    ) -> Iterator[tuple[TutorialRunner, SessionFactory, str]]:
        provider = MySQLCheckpointerProvider(recreate_schema(checkpointer_url()))
        provider.ensure_schema()
        factory = sessionmaker(bind=domain_engine, expire_on_commit=False, future=True)

        @contextmanager
        def scope() -> Iterator[Session]:
            with factory() as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        with scope() as session:
            run = make_run(session, project=make_project(session))
            created = run.id

        yield (
            TutorialRunner(provider=provider, session_factory=scope, owner="worker-1"),
            scope,
            created,
        )

    def test_a_run_survives_the_round_trip_through_both_stores(
        self, mysql_runner: tuple[TutorialRunner, SessionFactory, str]
    ) -> None:
        runner, session_factory, run_id = mysql_runner

        suspended = runner.start(run_id)
        assert suspended.status is RunStatus.SUSPENDED
        assert suspended.phase is RunPhase.HUMAN_INTERRUPT

        assert runner.start(run_id).skipped is True

        published = runner.resume(run_id, APPROVE)
        assert published.status is RunStatus.SUCCEEDED

        run = load_run(session_factory, run_id)
        assert run.status is RunStatus.SUCCEEDED
        assert run.phase is RunPhase.PUBLISH
        names = {step.name for step in steps_of(session_factory, run_id)}
        assert {"discover", "outline", "human_interrupt", "publish", "wake"} <= names
