from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from conftest import make_project, make_run
from sqlalchemy.orm import Session

from app.db.models import Artifact, Step
from app.db.models.enums import ArtifactKind, RunPhase, StepStatus
from app.runs.steps import DEFAULT_LEASE, ClaimOutcome, claim_step, complete_step, ensure_step
from app.storage.artifacts import (
    open_artifact,
    read_artifact,
    record_artifact,
    write_artifact,
)
from app.storage.content_store import (
    ArtifactIntegrityError,
    ContentAddressedStore,
    UnsafeArtifactPath,
)

PAGE = b"<html><body>Install the gateway</body></html>"


@pytest.fixture
def store(tmp_path: Path) -> ContentAddressedStore:
    return ContentAddressedStore(tmp_path / "artifacts")


def test_write_artifact_keeps_the_bytes_on_disk_and_only_metadata_in_the_database(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)

    artifact = write_artifact(
        db,
        store,
        PAGE,
        project_id=project.id,
        kind=ArtifactKind.RAW_HTML,
        media_type="text/html",
        provenance={"url": "https://docs.example.test/install", "fetched_at": "2026-03-04"},
    )

    assert artifact.sha256 == hashlib.sha256(PAGE).hexdigest()
    assert artifact.size_bytes == len(PAGE)
    assert artifact.storage_path == ContentAddressedStore.storage_path_for(artifact.sha256)
    assert artifact.provenance["url"] == "https://docs.example.test/install"
    assert store.read_bytes(artifact.storage_path) == PAGE
    assert PAGE.decode() not in str(
        db.execute(sa.text("SELECT * FROM artifacts")).mappings().all()
    )


def test_writing_the_same_content_twice_yields_one_row_and_one_file(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)
    kwargs = {"project_id": project.id, "kind": ArtifactKind.RAW_HTML, "media_type": "text/html"}

    first = write_artifact(db, store, PAGE, **kwargs)
    second = write_artifact(db, store, PAGE, **kwargs)
    db.commit()

    assert second.id == first.id
    assert db.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 1
    stored_files = [path for path in store.root.rglob("*") if path.is_file()]
    assert len(stored_files) == 1


def test_the_same_bytes_under_a_different_kind_are_tracked_separately(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)

    raw = write_artifact(db, store, PAGE, project_id=project.id, kind=ArtifactKind.RAW_HTML)
    corpus = write_artifact(
        db, store, PAGE, project_id=project.id, kind=ArtifactKind.NORMALIZED_CORPUS
    )

    assert raw.id != corpus.id
    assert raw.storage_path == corpus.storage_path


def test_write_artifact_accepts_a_stream_for_content_too_large_to_hold_in_memory(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)

    artifact = write_artifact(
        db,
        store,
        (b"chapter ", b"one ", b"markdown"),
        project_id=project.id,
        kind=ArtifactKind.CHAPTER_MARKDOWN,
    )

    assert read_artifact(store, artifact) == b"chapter one markdown"


def test_write_artifact_records_the_step_that_produced_it(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    run = make_run(db)
    step = ensure_step(db, run=run, name="snapshot", phase=RunPhase.SNAPSHOT)

    artifact = write_artifact(
        db,
        store,
        PAGE,
        project_id=run.project_id,
        kind=ArtifactKind.RAW_HTML,
        run_id=run.id,
        step_id=step.id,
    )

    assert artifact.run_id == run.id
    assert artifact.step_id == step.id


def test_read_artifact_detects_content_that_stopped_matching_its_recorded_digest(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)
    artifact = write_artifact(db, store, PAGE, project_id=project.id, kind=ArtifactKind.RAW_HTML)
    store.resolve(artifact.storage_path).write_bytes(b"tampered")

    with pytest.raises(ArtifactIntegrityError):
        read_artifact(store, artifact)


def test_read_artifact_can_skip_verification_for_large_streams(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)
    artifact = write_artifact(db, store, PAGE, project_id=project.id, kind=ArtifactKind.RAW_HTML)

    with open_artifact(store, artifact) as handle:
        assert handle.read() == PAGE


def test_a_tampered_storage_path_in_the_database_cannot_read_outside_the_root(
    db: Session,
    store: ContentAddressedStore,
    tmp_path: Path,
) -> None:
    """Defence in depth: the row is not trusted to describe a path inside the root."""
    project = make_project(db)
    (tmp_path / "secret.txt").write_text("do not leak", encoding="utf-8")
    artifact = write_artifact(db, store, PAGE, project_id=project.id, kind=ArtifactKind.RAW_HTML)
    artifact.storage_path = "../secret.txt"
    db.flush()

    with pytest.raises(UnsafeArtifactPath):
        read_artifact(store, artifact)


def test_record_artifact_is_reusable_for_bytes_that_are_already_stored(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    project = make_project(db)
    stored = store.put_bytes(PAGE)

    artifact = record_artifact(
        db,
        project_id=project.id,
        kind=ArtifactKind.RAW_HTML,
        stored=stored,
        provenance={"source": "replayed snapshot"},
    )

    assert artifact.storage_path == stored.storage_path
    assert artifact.size_bytes == stored.size_bytes


def test_a_worker_killed_after_writing_its_artifact_leaves_no_duplicate_on_retry(
    db: Session,
    store: ContentAddressedStore,
) -> None:
    """The at-least-once contract in practice: retrying re-does the write, not the row.

    The first worker writes the artifact and dies before marking the step succeeded. Once
    its lease expires the work is retried, and because the artifact is addressed by
    content the second attempt converges on the same file and the same row.
    """
    run = make_run(db)
    step = ensure_step(db, run=run, name="snapshot", phase=RunPhase.SNAPSHOT)
    from conftest import utcnow

    started = utcnow()

    assert claim_step(db, step, owner="worker-a", now=started).claimed
    first = write_artifact(
        db,
        store,
        PAGE,
        project_id=run.project_id,
        kind=ArtifactKind.RAW_HTML,
        run_id=run.id,
        step_id=step.id,
    )
    db.commit()

    retried_at = started + DEFAULT_LEASE + timedelta(seconds=1)
    retry = claim_step(db, step, owner="worker-b", now=retried_at)
    assert retry.outcome is ClaimOutcome.CLAIMED
    assert step.attempt == 2

    second = write_artifact(
        db,
        store,
        PAGE,
        project_id=run.project_id,
        kind=ArtifactKind.RAW_HTML,
        run_id=run.id,
        step_id=step.id,
    )
    complete_step(db, step, owner="worker-b", now=retried_at)
    db.commit()

    assert second.id == first.id
    assert db.scalar(sa.select(sa.func.count()).select_from(Artifact)) == 1
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 1
    assert db.get(Step, step.id).status is StepStatus.SUCCEEDED  # type: ignore[union-attr]
