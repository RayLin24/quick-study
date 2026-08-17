"""The mounted HTTP surface: create a run, approve an outline, export a bundle."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_outline, make_project, make_run, make_user
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth.csrf import CSRF_HEADER_NAME
from app.auth.sessions import create_session
from app.db.models.enums import ArtifactKind, RunPhase, RunStatus
from app.db.models.execution import unchecked_run_state
from app.db.session import get_session
from app.main import app
from app.storage.artifacts import write_artifact
from app.storage.content_store import ContentAddressedStore


@pytest.fixture
def store(tmp_path: Path) -> ContentAddressedStore:
    return ContentAddressedStore(tmp_path)


@pytest.fixture
def client(db: Session, store: ContentAddressedStore) -> Iterator[TestClient]:
    def override_session() -> Iterator[Session]:
        db.info["content_store"] = store
        yield db

    app.dependency_overrides[get_session] = override_session
    try:
        yield TestClient(app, base_url="https://testserver")
    finally:
        app.dependency_overrides.clear()


def _sign_in(client: TestClient, db: Session):
    user = make_user(db, email="owner@example.test")
    issued = create_session(db, user)
    db.commit()
    client.cookies.set("quickstudy_session", issued.token)
    return user, issued.csrf_token


def _headers(csrf: str) -> dict[str, str]:
    return {CSRF_HEADER_NAME: csrf}


class TestCreateAndReadRun:
    def test_creating_a_run_does_not_require_a_title_column(
        self, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _user, csrf = _sign_in(client, db)
        queued: list[str] = []
        monkeypatch.setattr("app.api.routes.runs.enqueue_start", queued.append)

        project = client.post(
            "/projects",
            headers=_headers(csrf),
            data={"name": "Flask", "slug": "flask-http"},
        )
        assert project.status_code == 201, project.text
        project_id = project.json()["id"]

        created = client.post(
            f"/projects/{project_id}/runs",
            headers=_headers(csrf),
            data={"title": "ignored"},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body["status"] == "running"
        assert body["phase"] == "discover"
        assert queued == [body["id"]]

        fetched = client.get(f"/projects/{project_id}/runs/{body['id']}")
        assert fetched.status_code == 200, fetched.text
        assert fetched.json()["title"] == "Flask"
        assert fetched.json()["status"] == "running"


class TestApprovals:
    def test_an_unknown_outline_is_404(self, client: TestClient, db: Session) -> None:
        user, csrf = _sign_in(client, db)
        project = make_project(db, owner=user)
        db.commit()
        response = client.post(
            f"/projects/{project.id}/approvals/missing-outline",
            headers=_headers(csrf),
            data={"decision": "approved"},
        )
        assert response.status_code == 404

    def test_deciding_an_outline_enqueues_a_resume(
        self, client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user, csrf = _sign_in(client, db)
        project = make_project(db, owner=user)
        run = make_run(db, project=project)
        outline = make_outline(db, run=run)
        db.commit()

        queued: list[tuple[str, dict[str, str]]] = []
        monkeypatch.setattr(
            "app.api.routes.approvals.enqueue_resume",
            lambda run_id, decision: queued.append((run_id, decision)),
        )

        response = client.post(
            f"/projects/{project.id}/approvals/{outline.id}",
            headers=_headers(csrf),
            data={"decision": "approved", "note": "ship it"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["decision"] == "approved"
        assert queued == [
            (
                run.id,
                {
                    "decision": "approved",
                    "note": "ship it",
                    "decided_by": user.id,
                },
            )
        ]


class TestExport:
    def test_a_succeeded_run_without_a_bundle_is_404(
        self, client: TestClient, db: Session
    ) -> None:
        user, _csrf = _sign_in(client, db)
        project = make_project(db, owner=user)
        run = make_run(db, project=project)
        with unchecked_run_state():
            run.status = RunStatus.SUCCEEDED
            run.phase = RunPhase.PUBLISH
        db.commit()

        response = client.get(f"/projects/{project.id}/exports/{run.id}/markdown")
        assert response.status_code == 404

    def test_a_published_bundle_is_streamed(
        self, client: TestClient, db: Session, store: ContentAddressedStore
    ) -> None:
        user, _csrf = _sign_in(client, db)
        project = make_project(db, owner=user)
        run = make_run(db, project=project)
        with unchecked_run_state():
            run.status = RunStatus.SUCCEEDED
            run.phase = RunPhase.PUBLISH
        write_artifact(
            db,
            store,
            b"PK\x03\x04stub-zip",
            project_id=project.id,
            kind=ArtifactKind.EXPORT_BUNDLE,
            media_type="application/zip",
            run_id=run.id,
        )
        db.commit()

        response = client.get(f"/projects/{project.id}/exports/{run.id}/markdown")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/zip")
        assert response.content.startswith(b"PK")
