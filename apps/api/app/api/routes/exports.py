"""Export endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Response, status

from app.auth.access import ProjectAccess, require_run_for_project
from app.auth.dependencies import DbSession, protected_router, require_project_role
from app.db.models.enums import ProjectRole, RunStatus

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
    run = require_run_for_project(session, project_id=project_id, run_id=run_id)
    if run.status is not RunStatus.SUCCEEDED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="the run has not produced a tutorial yet",
        )
    artifact = next(
        (artifact for artifact in run.artifacts if artifact.kind.value == "markdown_bundle"),
        None,
    )
    if artifact is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no Markdown bundle was published",
        )
    store = session.info["content_store"]
    return Response(
        content=store.read_bytes(artifact.storage_path),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{run.title or run.id}.zip"'},
    )
