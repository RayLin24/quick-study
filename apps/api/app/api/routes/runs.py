"""Run lifecycle endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, status

from app.auth.access import ProjectAccess, require_run_for_project
from app.auth.dependencies import (
    DbSession,
    access_errors_as_404,
    protected_router,
    require_project_role,
)
from app.db.models import Project, Run
from app.db.models.enums import ProjectRole, RunPhase
from app.runs.state_machine import start_run
from app.workflows.enqueue import enqueue_start
from app.workflows.publication import latest_outline, outline_payload

router = protected_router(prefix="/projects/{project_id}/runs", tags=["runs"])

ViewerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.VIEWER))]
EditorAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.EDITOR))]


@router.get("")
def list_runs(
    project_id: str,
    access: ViewerAccess,
    session: DbSession,
) -> list[dict[str, str]]:
    """List the runs of a project, newest first."""
    runs = (
        session.query(Run)
        .filter(Run.project_id == project_id)
        .order_by(Run.created_at.desc())
        .all()
    )
    return [
        {
            "id": run.id,
            "status": run.status.value,
            "phase": run.phase.value,
            "created_at": run.created_at.isoformat(),
        }
        for run in runs
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_run(
    project_id: str,
    access: EditorAccess,
    session: DbSession,
    title: Annotated[str, Form()] = "",
) -> dict[str, str]:
    """Start a tutorial generation run and wake the worker."""
    _ = title
    run = Run(project_id=project_id)
    session.add(run)
    session.flush()
    start_run(run, phase=RunPhase.DISCOVER)
    session.commit()
    enqueue_start(run.id)
    return {"id": run.id, "status": run.status.value, "phase": run.phase.value}


@router.get("/{run_id}")
def get_run(
    project_id: str,
    run_id: str,
    access: ViewerAccess,
    session: DbSession,
) -> dict[str, object]:
    """Return one run, scoped to the project."""
    with access_errors_as_404():
        run = require_run_for_project(session, project_id=project_id, run_id=run_id)
    project = session.get(Project, project_id)
    return {
        "id": run.id,
        "title": project.name if project is not None else "",
        "status": run.status.value,
        "phase": run.phase.value,
        "created_at": run.created_at.isoformat(),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "outline": outline_payload(latest_outline(session, run.id)),
    }
