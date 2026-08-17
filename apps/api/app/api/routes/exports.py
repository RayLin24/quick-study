"""Export endpoints."""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import Depends, HTTPException, Response, status

from app.auth.access import ProjectAccess, require_run_for_project
from app.auth.dependencies import (
    DbSession,
    access_errors_as_404,
    protected_router,
    require_project_role,
)
from app.db.models import Artifact
from app.db.models.enums import ArtifactKind, ProjectRole, RunStatus
from app.storage.content_store import build_content_store

router = protected_router(prefix="/projects/{project_id}/exports", tags=["exports"])

ViewerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.VIEWER))]


@router.get("/{run_id}/markdown")
def export_markdown(
    project_id: str,
    run_id: str,
    access: ViewerAccess,
    session: DbSession,
) -> Response:
    """Download the tutorial as a Markdown bundle.

    The bundle is produced by the workflow's publish step and stored as an artifact;
    this endpoint streams it. A run that has not published yet has nothing to export.
    """
    with access_errors_as_404():
        run = require_run_for_project(session, project_id=project_id, run_id=run_id)
    if run.status is not RunStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the run has not produced a tutorial yet",
        )
    artifact = session.scalars(
        sa.select(Artifact).where(
            Artifact.run_id == run.id,
            Artifact.kind == ArtifactKind.EXPORT_BUNDLE,
        )
    ).first()
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no Markdown bundle was published",
        )
    store = session.info.get("content_store") or build_content_store()
    filename = run.id
    return Response(
        content=store.read_bytes(artifact.storage_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}.zip"'},
    )
