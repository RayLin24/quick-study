"""Human approval endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, status

from app.auth.access import ProjectAccess, require_outline_for_project
from app.auth.dependencies import (
    CurrentSession,
    DbSession,
    protected_router,
    require_project_role,
)
from app.clock import utcnow
from app.db.models import Approval
from app.db.models.enums import ApprovalDecision, ApprovalSubject, ProjectRole

router = protected_router(prefix="/projects/{project_id}/approvals", tags=["approvals"])

EditorAccess = Annotated[ProjectAccess, Depends(require_project_role(ProjectRole.EDITOR))]


@router.post("/{outline_id}", status_code=status.HTTP_200_OK)
def decide_outline(
    project_id: str,
    outline_id: str,
    access: EditorAccess,
    current: CurrentSession,
    decision: Annotated[str, Form()],
    session: DbSession,
    note: Annotated[str, Form()] = "",
) -> dict[str, str]:
    """Approve, reject or request changes on an outline."""
    outline = require_outline_for_project(
        session, project_id=project_id, outline_id=outline_id
    )
    approval = Approval(
        project_id=project_id,
        run_id=outline.run_id,
        subject_type=ApprovalSubject.OUTLINE,
        subject_id=outline.id,
        decision=ApprovalDecision(decision),
        decided_by=current.user.id,
        decided_at=utcnow(),
        note=note,
    )
    session.add(approval)
    session.commit()
    return {
        "outline_id": outline.id,
        "decision": approval.decision.value,
        "note": approval.note,
    }
