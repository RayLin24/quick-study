from __future__ import annotations

import pytest
from conftest import (
    make_artifact,
    make_chapter,
    make_outline,
    make_project,
    make_run,
    make_step,
    make_user,
)
from sqlalchemy.orm import Session

from app.auth.access import (
    PROJECT_ROLE_RANK,
    ProjectAccessDenied,
    ProjectResourceNotFound,
    get_project_access,
    get_run_for_project,
    grant_project_role,
    require_artifact_for_project,
    require_chapter_for_project,
    require_outline_for_project,
    require_project_access,
    require_run_for_project,
    require_step_for_project,
    revoke_project_role,
    visible_projects,
)
from app.db.models import User
from app.db.models.enums import ProjectRole, UserRole


def test_roles_are_ordered_from_viewer_to_owner() -> None:
    assert PROJECT_ROLE_RANK[ProjectRole.VIEWER] < PROJECT_ROLE_RANK[ProjectRole.EDITOR]
    assert PROJECT_ROLE_RANK[ProjectRole.EDITOR] < PROJECT_ROLE_RANK[ProjectRole.OWNER]


def test_the_creator_of_a_project_owns_it(db: Session) -> None:
    project = make_project(db)
    owner = db.get(User, project.owner_id)
    assert owner is not None

    access = require_project_access(db, owner, project.id)

    assert access.role is ProjectRole.OWNER
    assert access.granted_by_admin is False


def test_a_stranger_has_no_access_at_all(db: Session) -> None:
    project = make_project(db)
    stranger = make_user(db)

    assert get_project_access(db, stranger, project.id) is None
    with pytest.raises(ProjectAccessDenied):
        require_project_access(db, stranger, project.id)


def test_a_granted_member_gets_exactly_the_role_they_were_given(db: Session) -> None:
    project = make_project(db)
    member = make_user(db)

    grant_project_role(db, project=project, user=member, role=ProjectRole.EDITOR)

    access = require_project_access(db, member, project.id)
    assert access.role is ProjectRole.EDITOR


def test_granting_again_updates_the_role_instead_of_duplicating_the_membership(
    db: Session,
) -> None:
    project = make_project(db)
    member = make_user(db)
    grant_project_role(db, project=project, user=member, role=ProjectRole.VIEWER)

    grant_project_role(db, project=project, user=member, role=ProjectRole.OWNER)

    assert require_project_access(db, member, project.id).role is ProjectRole.OWNER


def test_a_viewer_cannot_reach_an_editor_only_operation(db: Session) -> None:
    project = make_project(db)
    member = make_user(db)
    grant_project_role(db, project=project, user=member, role=ProjectRole.VIEWER)

    require_project_access(db, member, project.id, minimum=ProjectRole.VIEWER)
    with pytest.raises(ProjectAccessDenied):
        require_project_access(db, member, project.id, minimum=ProjectRole.EDITOR)


def test_an_editor_satisfies_a_viewer_requirement(db: Session) -> None:
    project = make_project(db)
    member = make_user(db)
    grant_project_role(db, project=project, user=member, role=ProjectRole.EDITOR)

    assert require_project_access(db, member, project.id, minimum=ProjectRole.VIEWER)


def test_revoking_a_membership_removes_the_access(db: Session) -> None:
    project = make_project(db)
    member = make_user(db)
    grant_project_role(db, project=project, user=member, role=ProjectRole.EDITOR)

    revoke_project_role(db, project=project, user=member)

    assert get_project_access(db, member, project.id) is None


def test_a_deployment_administrator_reaches_every_project(db: Session) -> None:
    project = make_project(db)
    admin = make_user(db, role=UserRole.ADMIN)

    access = require_project_access(db, admin, project.id, minimum=ProjectRole.OWNER)

    assert access.role is ProjectRole.OWNER
    assert access.granted_by_admin is True


def test_a_deactivated_administrator_reaches_nothing(db: Session) -> None:
    project = make_project(db)
    admin = make_user(db, role=UserRole.ADMIN, is_active=False)

    with pytest.raises(ProjectAccessDenied):
        require_project_access(db, admin, project.id)


def test_a_missing_project_is_indistinguishable_from_a_forbidden_one(db: Session) -> None:
    """Both answer the same way so project ids cannot be enumerated."""
    stranger = make_user(db)
    project = make_project(db)

    with pytest.raises(ProjectAccessDenied) as forbidden:
        require_project_access(db, stranger, project.id)
    with pytest.raises(ProjectAccessDenied) as missing:
        require_project_access(db, stranger, "0" * 32)

    assert str(forbidden.value) == str(missing.value)


def test_visible_projects_lists_only_the_ones_a_member_may_see(db: Session) -> None:
    member = make_user(db)
    mine = make_project(db)
    grant_project_role(db, project=mine, user=member, role=ProjectRole.VIEWER)
    make_project(db)
    db.flush()

    assert [project.id for project in visible_projects(db, member)] == [mine.id]


def test_visible_projects_lists_everything_for_an_administrator(db: Session) -> None:
    admin = make_user(db, role=UserRole.ADMIN)
    first = make_project(db)
    second = make_project(db)
    db.flush()

    assert {project.id for project in visible_projects(db, admin)} == {first.id, second.id}


def test_a_run_of_the_project_is_reachable_by_its_id(db: Session) -> None:
    run = make_run(db)

    assert require_run_for_project(db, project_id=run.project_id, run_id=run.id).id == run.id


def test_a_run_belonging_to_another_project_is_not_reachable(db: Session) -> None:
    """Authorising the project id in the path is worth nothing if the row ignores it."""
    mine = make_project(db)
    theirs = make_run(db)

    assert get_run_for_project(db, project_id=mine.id, run_id=theirs.id) is None
    with pytest.raises(ProjectResourceNotFound):
        require_run_for_project(db, project_id=mine.id, run_id=theirs.id)


def test_a_missing_row_and_a_foreign_row_answer_identically(db: Session) -> None:
    mine = make_project(db)
    theirs = make_run(db)

    with pytest.raises(ProjectResourceNotFound) as foreign:
        require_run_for_project(db, project_id=mine.id, run_id=theirs.id)
    with pytest.raises(ProjectResourceNotFound) as missing:
        require_run_for_project(db, project_id=mine.id, run_id="0" * 32)

    assert str(foreign.value) == str(missing.value)


def test_a_step_can_be_pinned_to_the_run_it_was_asked_for(db: Session) -> None:
    project = make_project(db)
    run = make_run(db, project=project)
    other_run = make_run(db, project=project)
    step = make_step(db, run=run)

    assert require_step_for_project(db, project_id=project.id, step_id=step.id).id == step.id
    with pytest.raises(ProjectResourceNotFound):
        require_step_for_project(
            db, project_id=project.id, step_id=step.id, run_id=other_run.id
        )


def test_a_step_belonging_to_another_project_is_not_reachable(db: Session) -> None:
    mine = make_project(db)
    step = make_step(db)

    with pytest.raises(ProjectResourceNotFound):
        require_step_for_project(db, project_id=mine.id, step_id=step.id)


def test_an_artifact_belonging_to_another_project_is_not_reachable(db: Session) -> None:
    mine = make_project(db)
    artifact = make_artifact(db)

    with pytest.raises(ProjectResourceNotFound):
        require_artifact_for_project(db, project_id=mine.id, artifact_id=artifact.id)
    assert (
        require_artifact_for_project(
            db, project_id=artifact.project_id, artifact_id=artifact.id
        ).id
        == artifact.id
    )


def test_an_outline_can_be_pinned_to_the_run_it_was_asked_for(db: Session) -> None:
    project = make_project(db)
    run = make_run(db, project=project)
    other_run = make_run(db, project=project)
    outline = make_outline(db, run=run)

    assert (
        require_outline_for_project(db, project_id=project.id, outline_id=outline.id).id
        == outline.id
    )
    with pytest.raises(ProjectResourceNotFound):
        require_outline_for_project(
            db, project_id=project.id, outline_id=outline.id, run_id=other_run.id
        )


def test_an_outline_belonging_to_another_project_is_not_reachable(db: Session) -> None:
    mine = make_project(db)
    outline = make_outline(db)

    with pytest.raises(ProjectResourceNotFound):
        require_outline_for_project(db, project_id=mine.id, outline_id=outline.id)


def test_a_chapter_can_be_pinned_to_the_outline_it_was_asked_for(db: Session) -> None:
    run = make_run(db)
    outline = make_outline(db, run=run)
    other_outline = make_outline(db, run=run, version=2)
    chapter = make_chapter(db, outline=outline)

    assert (
        require_chapter_for_project(
            db, project_id=outline.project_id, chapter_id=chapter.id
        ).id
        == chapter.id
    )
    with pytest.raises(ProjectResourceNotFound):
        require_chapter_for_project(
            db,
            project_id=outline.project_id,
            chapter_id=chapter.id,
            outline_id=other_outline.id,
        )


def test_a_chapter_belonging_to_another_project_is_not_reachable(db: Session) -> None:
    mine = make_project(db)
    chapter = make_chapter(db)

    with pytest.raises(ProjectResourceNotFound):
        require_chapter_for_project(db, project_id=mine.id, chapter_id=chapter.id)


def test_an_empty_identifier_reaches_nothing(db: Session) -> None:
    project = make_project(db)

    assert get_run_for_project(db, project_id=project.id, run_id="") is None
