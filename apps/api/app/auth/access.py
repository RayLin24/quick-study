from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import (
    Artifact,
    Chapter,
    Outline,
    Project,
    ProjectMember,
    Run,
    Step,
    User,
)
from app.db.models.enums import ProjectRole, UserRole

PROJECT_ROLE_RANK: Final[dict[ProjectRole, int]] = {
    ProjectRole.VIEWER: 0,
    ProjectRole.EDITOR: 1,
    ProjectRole.OWNER: 2,
}

#: One message for "no such project" and "not yours" so ids cannot be probed.
PROJECT_NOT_ACCESSIBLE: Final = "project not found"

#: The same answer for "no such row" and "not in this project", for the same reason.
RESOURCE_NOT_ACCESSIBLE: Final = "not found"


class AccessError(Exception):
    """Base class for authorisation failures."""


class ProjectAccessDenied(AccessError):
    """Raised when a caller may not act on a project. Maps to HTTP 404, never 403."""


class ProjectResourceNotFound(AccessError):
    """Raised when a row does not exist or belongs elsewhere. Maps to HTTP 404, never 403."""


@dataclass(frozen=True, slots=True)
class ProjectAccess:
    project: Project
    role: ProjectRole
    granted_by_admin: bool

    def allows(self, minimum: ProjectRole) -> bool:
        return PROJECT_ROLE_RANK[self.role] >= PROJECT_ROLE_RANK[minimum]


def get_project_access(session: Session, user: User, project_id: str) -> ProjectAccess | None:
    """Return the caller's effective role on a project, or nothing if they have none."""
    if not user.is_active:
        return None
    project = session.get(Project, project_id)
    if project is None:
        return None
    if user.role is UserRole.ADMIN:
        return ProjectAccess(project, ProjectRole.OWNER, granted_by_admin=True)
    if project.owner_id == user.id:
        return ProjectAccess(project, ProjectRole.OWNER, granted_by_admin=False)
    membership = _membership(session, project_id=project_id, user_id=user.id)
    if membership is None:
        return None
    return ProjectAccess(project, membership.role, granted_by_admin=False)


def require_project_access(
    session: Session,
    user: User,
    project_id: str,
    *,
    minimum: ProjectRole = ProjectRole.VIEWER,
) -> ProjectAccess:
    access = get_project_access(session, user, project_id)
    if access is None or not access.allows(minimum):
        raise ProjectAccessDenied(PROJECT_NOT_ACCESSIBLE)
    return access


def get_run_for_project(session: Session, *, project_id: str, run_id: str) -> Run | None:
    """Return the run only if it belongs to this project."""
    return _scoped(session, Run, run_id, project_id=project_id)


def require_run_for_project(session: Session, *, project_id: str, run_id: str) -> Run:
    return _required(get_run_for_project(session, project_id=project_id, run_id=run_id))


def get_step_for_project(
    session: Session,
    *,
    project_id: str,
    step_id: str,
    run_id: str | None = None,
) -> Step | None:
    """Return the step only if it belongs to this project, and to ``run_id`` when given."""
    return _scoped(session, Step, step_id, project_id=project_id, run_id=run_id)


def require_step_for_project(
    session: Session,
    *,
    project_id: str,
    step_id: str,
    run_id: str | None = None,
) -> Step:
    return _required(
        get_step_for_project(session, project_id=project_id, step_id=step_id, run_id=run_id)
    )


def get_artifact_for_project(
    session: Session,
    *,
    project_id: str,
    artifact_id: str,
) -> Artifact | None:
    return _scoped(session, Artifact, artifact_id, project_id=project_id)


def require_artifact_for_project(
    session: Session,
    *,
    project_id: str,
    artifact_id: str,
) -> Artifact:
    return _required(
        get_artifact_for_project(session, project_id=project_id, artifact_id=artifact_id)
    )


def get_outline_for_project(
    session: Session,
    *,
    project_id: str,
    outline_id: str,
    run_id: str | None = None,
) -> Outline | None:
    return _scoped(session, Outline, outline_id, project_id=project_id, run_id=run_id)


def require_outline_for_project(
    session: Session,
    *,
    project_id: str,
    outline_id: str,
    run_id: str | None = None,
) -> Outline:
    return _required(
        get_outline_for_project(
            session, project_id=project_id, outline_id=outline_id, run_id=run_id
        )
    )


def get_chapter_for_project(
    session: Session,
    *,
    project_id: str,
    chapter_id: str,
    outline_id: str | None = None,
) -> Chapter | None:
    return _scoped(session, Chapter, chapter_id, project_id=project_id, outline_id=outline_id)


def require_chapter_for_project(
    session: Session,
    *,
    project_id: str,
    chapter_id: str,
    outline_id: str | None = None,
) -> Chapter:
    return _required(
        get_chapter_for_project(
            session, project_id=project_id, chapter_id=chapter_id, outline_id=outline_id
        )
    )


def _scoped(
    session: Session,
    model: type[Any],
    resource_id: str,
    *,
    project_id: str,
    **parents: str | None,
) -> Any:
    """Load one row by id and drop it unless it sits where the caller says it does.

    Authorising the project in the path is worth nothing on its own: without this check a
    caller with one project can read every other project's rows by guessing their ids.
    """
    if not resource_id:
        return None
    row = session.get(model, resource_id)
    if row is None or row.project_id != project_id:
        return None
    for attribute, expected in parents.items():
        if expected is not None and getattr(row, attribute) != expected:
            return None
    return row


def _required(row: Any) -> Any:
    if row is None:
        raise ProjectResourceNotFound(RESOURCE_NOT_ACCESSIBLE)
    return row


def grant_project_role(
    session: Session,
    *,
    project: Project,
    user: User,
    role: ProjectRole,
) -> ProjectMember:
    """Give ``user`` a role on ``project``, replacing any role they already had."""
    membership = _membership(session, project_id=project.id, user_id=user.id)
    if membership is None:
        membership = ProjectMember(project_id=project.id, user_id=user.id, role=role)
        session.add(membership)
    else:
        membership.role = role
    session.flush()
    return membership


def revoke_project_role(session: Session, *, project: Project, user: User) -> None:
    membership = _membership(session, project_id=project.id, user_id=user.id)
    if membership is not None:
        session.delete(membership)
        session.flush()


def visible_projects(session: Session, user: User) -> Sequence[Project]:
    """List the projects a caller may open, newest last."""
    if not user.is_active:
        return []
    statement = sa.select(Project).order_by(Project.created_at, Project.id)
    if user.role is not UserRole.ADMIN:
        memberships = sa.select(ProjectMember.project_id).where(ProjectMember.user_id == user.id)
        statement = statement.where(
            sa.or_(Project.owner_id == user.id, Project.id.in_(memberships))
        )
    return session.scalars(statement).all()


def _membership(session: Session, *, project_id: str, user_id: str) -> ProjectMember | None:
    return session.scalars(
        sa.select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    ).one_or_none()
