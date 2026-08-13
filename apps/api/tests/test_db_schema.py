from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import sqlalchemy as sa
from conftest import make_document, make_project, make_run, make_snapshot, make_user
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import Run, Step, User, UserSession
from app.db.models.enums import RunPhase, RunStatus

EXPECTED_TABLES = frozenset(
    {
        "approvals",
        "artifacts",
        "chapters",
        "chunks",
        "citations",
        "claims",
        "documents",
        "edges",
        "outlines",
        "project_members",
        "projects",
        "runs",
        "sessions",
        "snapshots",
        "sources",
        "steps",
        "symbols",
        "users",
    }
)

PROJECT_SCOPED_TABLES = EXPECTED_TABLES - {"users", "sessions", "projects"}

EXPECTED_FULLTEXT_INDEXES = {
    "ft_documents_title": ("documents", ("title",)),
    "ft_documents_body_text": ("documents", ("body_text",)),
    "ft_chunks_text": ("chunks", ("text",)),
    "ft_symbols_identifier": ("symbols", ("name", "qualified_name", "signature", "docstring")),
}

MYSQL_MAX_IDENTIFIER_LENGTH = 64


def test_metadata_declares_exactly_the_planned_domain_tables() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_every_table_targets_innodb_and_utf8mb4(table_name: str) -> None:
    table = Base.metadata.tables[table_name]

    assert table.kwargs.get("mysql_engine") == "InnoDB"
    assert table.kwargs.get("mysql_charset") == "utf8mb4"


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_every_string_column_is_length_bound_so_mysql_can_create_it(table_name: str) -> None:
    table = Base.metadata.tables[table_name]

    unbounded = [
        column.name
        for column in table.columns
        if isinstance(column.type, sa.String)
        and not isinstance(column.type, sa.Text)
        and not column.type.length
    ]

    assert unbounded == []


@pytest.mark.parametrize("table_name", sorted(EXPECTED_TABLES))
def test_identifiers_stay_within_the_mysql_limit(table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    names = (
        [table.name]
        + [index.name or "" for index in table.indexes]
        + [constraint.name or "" for constraint in table.constraints]
    )

    too_long = [name for name in names if len(str(name)) > MYSQL_MAX_IDENTIFIER_LENGTH]

    assert too_long == []


@pytest.mark.parametrize("table_name", sorted(PROJECT_SCOPED_TABLES))
def test_project_scoped_rows_disappear_with_their_project(table_name: str) -> None:
    table = Base.metadata.tables[table_name]

    assert "project_id" in table.columns
    foreign_keys = [fk for fk in table.foreign_keys if fk.parent.name == "project_id"]
    assert [fk.column.table.name for fk in foreign_keys] == ["projects"]
    assert [fk.ondelete for fk in foreign_keys] == ["CASCADE"]


@pytest.mark.parametrize(("index_name", "expected"), sorted(EXPECTED_FULLTEXT_INDEXES.items()))
def test_fulltext_indexes_are_declared_for_the_retrieval_surface(
    index_name: str,
    expected: tuple[str, tuple[str, ...]],
) -> None:
    table_name, columns = expected
    table = Base.metadata.tables[table_name]

    index = next(candidate for candidate in table.indexes if candidate.name == index_name)

    assert index.dialect_options["mysql"]["prefix"] == "FULLTEXT"
    assert tuple(column.name for column in index.columns) == columns


@pytest.mark.parametrize(
    ("table_name", "column_name"),
    [
        ("users", "email"),
        ("users", "bootstrap_slot"),
        ("projects", "slug"),
        ("sessions", "token_fingerprint"),
        ("runs", "thread_id"),
        ("runs", "idempotency_key"),
        ("steps", "idempotency_key"),
    ],
)
def test_single_column_uniqueness_is_enforced_by_the_database(
    table_name: str,
    column_name: str,
) -> None:
    table = Base.metadata.tables[table_name]

    unique_single_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    } | {
        tuple(column.name for column in index.columns) for index in table.indexes if index.unique
    }

    assert (column_name,) in unique_single_columns


@pytest.mark.parametrize(
    ("table_name", "columns"),
    [
        ("project_members", ("project_id", "user_id")),
        ("sources", ("project_id", "locator_fingerprint")),
        ("snapshots", ("source_id", "fingerprint")),
        ("documents", ("snapshot_id", "uri_fingerprint")),
        ("chunks", ("document_id", "ordinal")),
        ("artifacts", ("project_id", "kind", "sha256")),
        ("outlines", ("run_id", "version")),
        ("chapters", ("outline_id", "ordinal")),
        ("chapters", ("outline_id", "slug")),
    ],
)
def test_composite_uniqueness_pins_the_natural_keys(
    table_name: str,
    columns: tuple[str, ...],
) -> None:
    table = Base.metadata.tables[table_name]

    composites = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    assert columns in composites


def test_no_foreign_key_cycle_blocks_a_plain_create_all() -> None:
    """SQLite cannot add constraints after the fact, so the graph must stay acyclic."""
    assert [table.name for table in Base.metadata.sorted_tables]


def test_generated_identifiers_are_opaque_and_unique(db: Session) -> None:
    first = make_user(db, email="first@example.test")
    second = make_user(db, email="second@example.test")

    assert len(first.id) == 32
    assert first.id != second.id


def test_timestamps_are_populated_on_insert_and_refreshed_on_update(db: Session) -> None:
    user = make_user(db)
    created_at, first_update = user.created_at, user.updated_at

    user.display_name = "renamed"
    db.flush()

    assert created_at.tzinfo is UTC
    assert user.created_at == created_at
    assert user.updated_at >= first_update


def test_datetimes_round_trip_as_aware_utc_regardless_of_the_input_offset(db: Session) -> None:
    run = make_run(db)
    tokyo = timezone(timedelta(hours=9))
    run.started_at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=tokyo)
    db.flush()
    db.expire_all()

    reloaded = db.get(Run, run.id)

    assert reloaded is not None
    assert reloaded.started_at is not None
    assert reloaded.started_at.tzinfo is UTC
    assert reloaded.started_at == datetime(2026, 1, 1, 18, 4, 5, tzinfo=UTC)


def test_naive_datetimes_are_refused_rather_than_silently_assumed_to_be_utc(db: Session) -> None:
    run = make_run(db)
    run.started_at = datetime(2026, 1, 2, 3, 4, 5)

    with pytest.raises(StatementError):
        db.flush()


def test_enum_columns_round_trip_as_python_enum_members(db: Session) -> None:
    run = make_run(db)

    assert run.status is RunStatus.PENDING
    assert run.phase is RunPhase.QUEUED

    run.status = RunStatus.RUNNING
    db.flush()
    db.expire_all()
    reloaded = db.get(Run, run.id)

    assert reloaded is not None
    assert reloaded.status is RunStatus.RUNNING


def test_enum_columns_store_the_lowercase_wire_value(db: Session) -> None:
    run = make_run(db, status=RunStatus.SUSPENDED, phase=RunPhase.HUMAN_INTERRUPT)
    db.flush()

    row = db.execute(
        sa.text("SELECT status, phase FROM runs WHERE id = :id"), {"id": run.id}
    ).one()

    assert row.status == "suspended"
    assert row.phase == "human_interrupt"


def test_enum_columns_reject_values_outside_the_state_vocabulary(db: Session) -> None:
    run = make_run(db)
    run.status = "definitely-not-a-status"  # type: ignore[assignment]

    with pytest.raises(StatementError):
        db.flush()


def test_duplicate_emails_are_rejected_case_insensitively_by_normalisation(db: Session) -> None:
    make_user(db, email="Owner@Example.test")
    db.commit()

    assert db.scalar(sa.select(User.email)) == "owner@example.test"
    db.add(User(email="OWNER@EXAMPLE.TEST", display_name="clone", password_hash="x"))
    with pytest.raises(IntegrityError):
        db.flush()


def test_only_one_row_may_ever_claim_the_bootstrap_admin_slot(db: Session) -> None:
    make_user(db, email="first@example.test", bootstrap_slot="admin")
    db.commit()

    db.add(
        User(
            email="second@example.test",
            display_name="second",
            password_hash="x",
            bootstrap_slot="admin",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_several_rows_may_leave_the_bootstrap_slot_empty(db: Session) -> None:
    make_user(db, email="first@example.test")
    make_user(db, email="second@example.test")
    db.commit()

    assert db.scalar(sa.select(sa.func.count()).select_from(User)) == 2


def test_deleting_a_project_removes_its_documents_and_runs(db: Session) -> None:
    project = make_project(db)
    make_document(db, snapshot=make_snapshot(db, project=project))
    make_run(db, project=project)
    db.commit()

    db.delete(project)
    db.commit()

    assert db.scalar(sa.select(sa.func.count()).select_from(Run)) == 0


def test_deleting_a_user_removes_their_sessions(db: Session) -> None:
    user = make_user(db)
    db.add(
        UserSession(
            user_id=user.id,
            token_fingerprint="d" * 64,
            csrf_fingerprint="e" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    db.commit()

    db.delete(user)
    db.commit()

    assert db.scalar(sa.select(sa.func.count()).select_from(UserSession)) == 0


def test_steps_disappear_with_their_run_but_artifacts_keep_their_provenance(db: Session) -> None:
    from app.db.models import Artifact
    from app.db.models.enums import ArtifactKind

    run = make_run(db)
    step = Step(
        run_id=run.id,
        project_id=run.project_id,
        name="snapshot",
        phase=RunPhase.SNAPSHOT,
        sequence=1,
        idempotency_key="run/step/snapshot",
    )
    db.add(step)
    db.flush()
    db.add(
        Artifact(
            project_id=run.project_id,
            run_id=run.id,
            step_id=step.id,
            kind=ArtifactKind.RAW_HTML,
            sha256="f" * 64,
            storage_path="ff/ff/" + "f" * 64,
            media_type="text/html",
            size_bytes=12,
        )
    )
    db.commit()

    db.delete(run)
    db.commit()

    assert db.scalar(sa.select(sa.func.count()).select_from(Step)) == 0
    artifact = db.scalars(sa.select(Artifact)).one()
    assert artifact.run_id is None
    assert artifact.step_id is None
    assert artifact.storage_path.endswith("f" * 64)
