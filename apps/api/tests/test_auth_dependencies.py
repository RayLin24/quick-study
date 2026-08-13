"""The HTTP-facing half of authentication.

No routes ship in this phase, so these tests mount the dependencies on a throwaway app.
That is what proves the cookie flags, the 401 path and the CSRF rule actually hold when
FastAPI wires them together, instead of only asserting on helper return values.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import anyio
import httpx
import pytest
from conftest import make_project, make_run, make_user
from fastapi import Depends, FastAPI, Response
from sqlalchemy.orm import Session

from app.auth.access import ProjectRole, grant_project_role, require_run_for_project
from app.auth.csrf import CSRF_HEADER_NAME
from app.auth.dependencies import (
    CurrentSession,
    DbSession,
    access_errors_as_404,
    protected_router,
    require_csrf,
    require_project_role,
    routes_missing_csrf,
)
from app.auth.sessions import clear_session_cookie, create_session, set_session_cookie
from app.db.models import User
from app.db.session import get_session
from app.settings import Settings, get_settings

NOW = datetime(2026, 8, 9, 10, 11, 12, tzinfo=UTC)


class AsgiClient:
    """A tiny synchronous client over the ASGI app.

    Cookies are passed explicitly rather than through a jar so each test states exactly
    which credential it presents.
    """

    def __init__(self, app: FastAPI, cookie_name: str) -> None:
        self.app = app
        self._cookie_name = cookie_name

    def request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        all_headers = dict(headers or {})
        if token is not None:
            all_headers["Cookie"] = f"{self._cookie_name}={token}"

        async def send() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                return await client.request(method, path, headers=all_headers)

        return anyio.run(send)

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(db: Session, settings: Settings) -> AsgiClient:
    app = FastAPI()

    @app.get("/whoami")
    def whoami(authenticated: CurrentSession) -> dict[str, str]:
        return {"email": authenticated.user.email}

    @app.post("/things", dependencies=[Depends(require_csrf)])
    def create_thing(authenticated: CurrentSession) -> dict[str, str]:
        return {"created_by": authenticated.user.email}

    @app.get("/projects/{project_id}/editable")
    def editable(
        project_id: str,
        _access: Any = Depends(require_project_role(ProjectRole.EDITOR)),
    ) -> dict[str, str]:
        return {"project_id": project_id}

    @app.post("/login")
    def login(response: Response) -> dict[str, str]:
        user = make_user(db, email="cookie@example.test")
        issued = create_session(db, user, now=NOW)
        db.commit()
        set_session_cookie(response, issued.token, settings=settings)
        return {"csrf_token": issued.csrf_token}

    @app.post("/logout")
    def logout(response: Response) -> dict[str, str]:
        clear_session_cookie(response, settings=settings)
        return {"status": "ok"}

    guarded = protected_router(prefix="/projects")

    @guarded.post("/{project_id}/runs")
    def start_run(project_id: str) -> dict[str, str]:
        return {"project_id": project_id}

    @guarded.get("/{project_id}/runs/{run_id}")
    def read_run(project_id: str, run_id: str, session: DbSession) -> dict[str, str]:
        with access_errors_as_404():
            run = require_run_for_project(session, project_id=project_id, run_id=run_id)
        return {"run_id": run.id}

    app.include_router(guarded)
    app.dependency_overrides[get_session] = lambda: db
    app.dependency_overrides[get_settings] = lambda: settings
    return AsgiClient(app, settings.session_cookie_name)


def sign_in(db: Session, **user_overrides: Any) -> tuple[User, str, str]:
    """Create a user with a live session; returns the user, its cookie and CSRF token."""
    user = make_user(db, **user_overrides)
    issued = create_session(db, user, now=datetime.now(UTC))
    db.commit()
    return user, issued.token, issued.csrf_token


def test_a_request_without_a_session_cookie_is_unauthorised(client: AsgiClient) -> None:
    assert client.get("/whoami").status_code == 401


def test_a_request_with_an_unknown_session_cookie_is_unauthorised(client: AsgiClient) -> None:
    assert client.get("/whoami", token="not-a-real-token").status_code == 401


def test_a_valid_session_cookie_identifies_the_caller(
    client: AsgiClient,
    db: Session,
) -> None:
    _, token, _ = sign_in(db, email="reader@example.test")

    response = client.get("/whoami", token=token)

    assert response.status_code == 200
    assert response.json()["email"] == "reader@example.test"


def test_an_expired_session_cookie_is_unauthorised(client: AsgiClient, db: Session) -> None:
    user = make_user(db)
    issued = create_session(db, user, now=datetime.now(UTC) - timedelta(days=30))
    db.commit()

    assert client.get("/whoami", token=issued.token).status_code == 401


def test_a_revoked_session_cookie_is_unauthorised(client: AsgiClient, db: Session) -> None:
    from app.auth.sessions import revoke_session

    user = make_user(db)
    issued = create_session(db, user, now=datetime.now(UTC))
    revoke_session(db, issued.record)
    db.commit()

    assert client.get("/whoami", token=issued.token).status_code == 401


def test_the_login_response_sets_an_httponly_samesite_cookie(client: AsgiClient) -> None:
    response = client.post("/login")

    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie
    assert "Secure" in cookie
    assert "Path=/" in cookie
    assert response.json()["csrf_token"] not in cookie


def test_logging_out_clears_the_cookie(client: AsgiClient) -> None:
    cookie = client.post("/logout").headers["set-cookie"]

    assert "HttpOnly" in cookie
    assert "Max-Age=0" in cookie or "expires=thu, 01 jan 1970" in cookie.lower()


def test_a_state_changing_request_without_a_csrf_token_is_rejected(
    client: AsgiClient,
    db: Session,
) -> None:
    _, token, _ = sign_in(db)

    response = client.post("/things", token=token)

    assert response.status_code == 403
    assert "csrf" in response.json()["detail"].lower()


def test_a_state_changing_request_with_the_wrong_csrf_token_is_rejected(
    client: AsgiClient,
    db: Session,
) -> None:
    _, token, _ = sign_in(db)

    response = client.post(
        "/things", token=token, headers={CSRF_HEADER_NAME: "borrowed-from-elsewhere"}
    )

    assert response.status_code == 403


def test_a_state_changing_request_with_the_session_csrf_token_is_accepted(
    client: AsgiClient,
    db: Session,
) -> None:
    _, token, csrf_token = sign_in(db, email="writer@example.test")

    response = client.post("/things", token=token, headers={CSRF_HEADER_NAME: csrf_token})

    assert response.status_code == 200
    assert response.json()["created_by"] == "writer@example.test"


def test_a_csrf_token_from_another_session_is_rejected(
    client: AsgiClient,
    db: Session,
) -> None:
    _, _, other_csrf_token = sign_in(db)
    _, token, _ = sign_in(db)

    response = client.post("/things", token=token, headers={CSRF_HEADER_NAME: other_csrf_token})

    assert response.status_code == 403


def test_a_read_only_request_needs_no_csrf_token(client: AsgiClient, db: Session) -> None:
    _, token, _ = sign_in(db)

    assert client.get("/whoami", token=token).status_code == 200


def test_an_unauthenticated_state_change_never_reaches_the_csrf_check(
    client: AsgiClient,
) -> None:
    assert client.post("/things").status_code == 401


def test_project_scoped_routes_refuse_a_caller_without_the_required_role(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    _, token, _ = sign_in(db)

    response = client.get(f"/projects/{project.id}/editable", token=token)

    assert response.status_code == 404


def test_project_scoped_routes_refuse_a_viewer_where_an_editor_is_required(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    user, token, _ = sign_in(db)
    grant_project_role(db, project=project, user=user, role=ProjectRole.VIEWER)
    db.commit()

    assert client.get(f"/projects/{project.id}/editable", token=token).status_code == 404


def test_project_scoped_routes_admit_a_caller_with_the_required_role(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    user, token, _ = sign_in(db, email="editor@example.test")
    grant_project_role(db, project=project, user=user, role=ProjectRole.EDITOR)
    db.commit()

    response = client.get(f"/projects/{project.id}/editable", token=token)

    assert response.status_code == 200
    assert response.json()["project_id"] == project.id


def test_a_route_that_forgets_the_csrf_dependency_is_reported() -> None:
    """The audit is the safety net: nobody has to remember on every new endpoint."""
    app = FastAPI()

    @app.post("/things")
    def create() -> dict[str, str]:
        return {}

    @app.delete("/things/{thing_id}")
    def remove(thing_id: str) -> dict[str, str]:
        return {}

    @app.get("/things")
    def index() -> dict[str, str]:
        return {}

    assert routes_missing_csrf(app) == ["DELETE /things/{thing_id}", "POST /things"]


def test_no_route_of_a_protected_router_can_forget_the_csrf_check() -> None:
    app = FastAPI()
    router = protected_router(prefix="/widgets")

    @router.post("/")
    def create() -> dict[str, str]:
        return {}

    @router.patch("/{widget_id}")
    def update(widget_id: str) -> dict[str, str]:
        return {}

    app.include_router(router)

    assert routes_missing_csrf(app) == []


def test_the_sample_application_only_exempts_its_public_entry_points(
    client: AsgiClient,
) -> None:
    assert routes_missing_csrf(client.app) == ["POST /login", "POST /logout"]


def test_a_protected_router_rejects_a_state_change_without_the_token(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    _, token, _ = sign_in(db)

    response = client.post(f"/projects/{project.id}/runs", token=token)

    assert response.status_code == 403


def test_a_protected_router_accepts_the_state_change_with_the_token(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    _, token, csrf_token = sign_in(db)

    response = client.post(
        f"/projects/{project.id}/runs", token=token, headers={CSRF_HEADER_NAME: csrf_token}
    )

    assert response.status_code == 200


def test_a_row_from_another_project_answers_404_rather_than_leaking_it(
    client: AsgiClient,
    db: Session,
) -> None:
    project = make_project(db)
    foreign_run = make_run(db)
    _, token, _ = sign_in(db)
    db.commit()

    response = client.get(f"/projects/{project.id}/runs/{foreign_run.id}", token=token)

    assert response.status_code == 404


def test_a_row_of_the_project_is_served(client: AsgiClient, db: Session) -> None:
    project = make_project(db)
    run = make_run(db, project=project)
    _, token, _ = sign_in(db)
    db.commit()

    response = client.get(f"/projects/{project.id}/runs/{run.id}", token=token)

    assert response.status_code == 200
    assert response.json()["run_id"] == run.id
