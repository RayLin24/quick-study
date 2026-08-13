from __future__ import annotations

from collections.abc import Iterable
from typing import IO, Any, Final

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Artifact
from app.db.models.enums import ArtifactKind
from app.storage.content_store import ContentAddressedStore, StoredArtifact

DEFAULT_MEDIA_TYPE: Final = "application/octet-stream"


def record_artifact(
    session: Session,
    *,
    project_id: str,
    kind: ArtifactKind,
    stored: StoredArtifact,
    media_type: str = DEFAULT_MEDIA_TYPE,
    provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
) -> Artifact:
    """Record already-stored bytes, reusing the row when this content is known.

    A retried step converges on the same row because the digest, not the attempt, is the
    identity. Provenance from the first successful write is kept: it describes where the
    bytes actually came from.
    """
    existing = _find(session, project_id=project_id, kind=kind, sha256=stored.sha256)
    if existing is not None:
        return existing

    artifact = Artifact(
        project_id=project_id,
        run_id=run_id,
        step_id=step_id,
        kind=kind,
        sha256=stored.sha256,
        storage_path=stored.storage_path,
        media_type=media_type,
        size_bytes=stored.size_bytes,
        provenance=provenance or {},
    )
    savepoint = session.begin_nested()
    try:
        session.add(artifact)
        session.flush()
    except IntegrityError:
        savepoint.rollback()
        raced = _find(session, project_id=project_id, kind=kind, sha256=stored.sha256)
        if raced is None:
            raise
        return raced
    savepoint.commit()
    return artifact


def write_artifact(
    session: Session,
    store: ContentAddressedStore,
    payload: bytes | Iterable[bytes],
    *,
    project_id: str,
    kind: ArtifactKind,
    media_type: str = DEFAULT_MEDIA_TYPE,
    provenance: dict[str, Any] | None = None,
    run_id: str | None = None,
    step_id: str | None = None,
) -> Artifact:
    """Store bytes and record their metadata. Safe to repeat with the same content."""
    stored = (
        store.put_bytes(payload) if isinstance(payload, bytes) else store.put_stream(payload)
    )
    return record_artifact(
        session,
        project_id=project_id,
        kind=kind,
        stored=stored,
        media_type=media_type,
        provenance=provenance,
        run_id=run_id,
        step_id=step_id,
    )


def open_artifact(store: ContentAddressedStore, artifact: Artifact) -> IO[bytes]:
    """Open the payload for streaming. The path is re-validated against the root."""
    return store.open(artifact.storage_path)


def read_artifact(
    store: ContentAddressedStore,
    artifact: Artifact,
    *,
    verify: bool = True,
) -> bytes:
    """Read the payload, by default checking it still hashes to the recorded digest."""
    if verify:
        store.verify(artifact.storage_path, artifact.sha256)
    return store.read_bytes(artifact.storage_path)


def _find(
    session: Session,
    *,
    project_id: str,
    kind: ArtifactKind,
    sha256: str,
) -> Artifact | None:
    return session.scalars(
        sa.select(Artifact).where(
            Artifact.project_id == project_id,
            Artifact.kind == kind,
            Artifact.sha256 == sha256,
        )
    ).one_or_none()
