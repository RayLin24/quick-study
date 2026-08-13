"""Chapter read and edit endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, status

from app.auth.access import ProjectAccess, require_chapter_for_project
from app.auth.dependencies import (
    CurrentSession,
    DbSession,
    protected_router,
    require_project_role,
)
from app.clock import utcnow
from app.db.models.enums import ChapterStatus, ProjectRole

router = protected_router(prefix="/projects/{project_id}/chapters", tags=["chapters"])

ViewerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.VIEWER))]
EditorAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.EDITOR))]


@router.get("/{chapter_id}")
def get_chapter(
    project_id: str,
    chapter_id: str,
    access: ViewerAccess,
    session: DbSession,
) -> dict[str, str | None]:
    """Return one chapter, scoped to the project."""
    chapter = require_chapter_for_project(
        session, project_id=project_id, chapter_id=chapter_id
    )
    return {
        "id": chapter.id,
        "slug": chapter.slug,
        "title": chapter.title,
        "status": chapter.status.value,
        "summary": chapter.summary,
        "revision": str(chapter.revision),
        "locked": str(chapter.locked_at is not None).lower(),
    }


@router.post("/{chapter_id}/lock", status_code=status.HTTP_200_OK)
def lock_chapter(
    project_id: str,
    chapter_id: str,
    access: EditorAccess,
    current: CurrentSession,
    session: DbSession,
) -> dict[str, str]:
    """Mark a chapter as accepted; a locked chapter is never overwritten by regeneration."""
    chapter = require_chapter_for_project(
        session, project_id=project_id, chapter_id=chapter_id
    )
    if chapter.locked_at is None:
        chapter.locked_at = utcnow()
        chapter.locked_by = current.user.id
        chapter.status = ChapterStatus.APPROVED
    session.commit()
    return {"id": chapter.id, "locked": "true"}
