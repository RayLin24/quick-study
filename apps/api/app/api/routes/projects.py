"""Project management endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, status

from app.auth.access import ProjectAccess, visible_projects
from app.auth.dependencies import (
    CurrentSession,
    DbSession,
    protected_router,
    require_project_role,
)
from app.db.models import Project
from app.db.models.enums import ProjectRole

router = protected_router(prefix="/projects", tags=["projects"])

ViewerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.VIEWER))]
EditorAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.EDITOR))]
OwnerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.OWNER))]


@router.get("")
def list_projects(
    current: CurrentSession,
    session: DbSession,
) -> list[dict[str, str]]:
    """List the projects the caller may open."""
    return [
        {
            "id": project.id,
            "name": project.name,
            "slug": project.slug,
            "output_language": project.output_language,
        }
        for project in visible_projects(session, current.user)
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    current: CurrentSession,
    name: Annotated[str, Form()],
    slug: Annotated[str, Form()],
    session: DbSession,
    output_language: Annotated[str, Form()] = "zh",
    reader_level: Annotated[str, Form()] = "intermediate",
    length_preset: Annotated[str, Form()] = "standard",
) -> dict[str, str]:
    """Create a project owned by the caller."""
    project = Project(
        owner_id=current.user.id,
        name=name,
        slug=slug,
        output_language=output_language,
        reader_level=reader_level,
        length_preset=length_preset,
    )
    session.add(project)
    session.commit()
    return {"id": project.id, "slug": project.slug}


@router.get("/{project_id}")
def get_project(
    project_id: str,
    access: ViewerAccess,
) -> dict[str, str]:
    """Return one project the caller may open."""
    project = access.project
    return {
        "id": project.id,
        "name": project.name,
        "slug": project.slug,
        "output_language": project.output_language,
        "reader_level": project.reader_level.value,
        "length_preset": project.length_preset.value,
    }


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    access: OwnerAccess,
    session: DbSession,
) -> None:
    """Remove a project and everything under it."""
    session.delete(access.project)
    session.commit()
