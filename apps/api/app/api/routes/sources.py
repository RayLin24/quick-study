"""Source submission endpoints."""

from __future__ import annotations

import hashlib
from typing import Annotated

from fastapi import Depends, Form, status

from app.auth.access import ProjectAccess
from app.auth.dependencies import DbSession, protected_router, require_project_role
from app.db.models import Source
from app.db.models.enums import ProjectRole, SourceKind

router = protected_router(prefix="/projects/{project_id}/sources", tags=["sources"])

ViewerAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.VIEWER))]
EditorAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.EDITOR))]


@router.get("")
def list_sources(
    project_id: str,
    access: ViewerAccess,
    session: DbSession,
) -> list[dict[str, str]]:
    """List the sources submitted for a project."""
    return [
        {
            "id": source.id,
            "kind": source.kind.value,
            "locator": source.locator,
            "display_name": source.display_name,
        }
        for source in session.query(Source).filter(Source.project_id == project_id).all()
    ]


@router.post("", status_code=status.HTTP_201_CREATED)
def add_source(
    project_id: str,
    access: EditorAccess,
    kind: Annotated[str, Form()],
    locator: Annotated[str, Form()],
    session: DbSession,
    display_name: Annotated[str, Form()] = "",
) -> dict[str, str]:
    """Register a documentation site or repository to learn from."""
    fingerprint = hashlib.sha256(locator.encode("utf-8")).hexdigest()
    source = Source(
        project_id=project_id,
        kind=SourceKind(kind),
        locator=locator,
        locator_fingerprint=fingerprint,
        display_name=display_name,
    )
    session.add(source)
    session.commit()
    return {
        "id": source.id,
        "kind": source.kind.value,
        "locator": source.locator,
        "display_name": source.display_name,
    }
