from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.access import (
    RESOURCE_NOT_ACCESSIBLE,
    AccessError,
    ProjectAccess,
    ProjectAccessDenied,
    require_project_access,
)
from app.auth.csrf import CSRF_HEADER_NAME, method_requires_csrf, verify_csrf_token
from app.auth.sessions import AuthenticatedSession, authenticate_session
from app.db.models.enums import ProjectRole
from app.db.session import get_session
from app.settings import Settings, get_settings

NOT_AUTHENTICATED: Final = "not authenticated"
CSRF_REJECTED: Final = f"missing or invalid CSRF token; send it in {CSRF_HEADER_NAME}"
PROJECT_NOT_FOUND: Final = "project not found"

DbSession = Annotated[Session, Depends(get_session)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def require_session(
    request: Request,
    session: DbSession,
    settings: AppSettings,
) -> AuthenticatedSession:
    """Resolve the session cookie or reject the request."""
    token = request.cookies.get(settings.session_cookie_name)
    authenticated = authenticate_session(session, token)
    if authenticated is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, NOT_AUTHENTICATED)
    return authenticated


CurrentSession = Annotated[AuthenticatedSession, Depends(require_session)]


def require_csrf(request: Request, authenticated: CurrentSession) -> None:
    """Require the session's CSRF token on every state-changing request.

    The token is bound to the session row and handed out in a response body, so a
    cross-site page cannot read it even though the browser will attach the cookie.
    """
    if not method_requires_csrf(request.method):
        return
    presented = request.headers.get(CSRF_HEADER_NAME)
    if not verify_csrf_token(authenticated.record.csrf_fingerprint, presented):
        raise HTTPException(status.HTTP_403_FORBIDDEN, CSRF_REJECTED)


def protected_router(**options: Any) -> APIRouter:
    """An ``APIRouter`` whose every route authenticates and enforces CSRF.

    Mounting the check once on the router is what stops a new endpoint from shipping
    without it. ``require_csrf`` returns immediately on a safe method, so read routes pay
    only for resolving the session, which they need anyway.
    """
    dependencies = [*options.pop("dependencies", []), Depends(require_csrf)]
    return APIRouter(dependencies=dependencies, **options)


def routes_missing_csrf(app: FastAPI) -> list[str]:
    """Return the state-changing routes that never reach :func:`require_csrf`.

    Assert on this in a test rather than trusting review: the only routes that legitimately
    appear here are the public entry points that have no session to bind a token to -- the
    login and bootstrap flows -- and a list makes those a deliberate, visible exception.
    """
    missing: list[str] = []
    for route in app.routes:
        unsafe = sorted(
            method
            for method in (getattr(route, "methods", None) or ())
            if method_requires_csrf(method)
        )
        if not unsafe or _depends_on(getattr(route, "dependant", None), require_csrf):
            continue
        missing.extend(f"{method} {route.path}" for method in unsafe)
    return sorted(missing)


@contextmanager
def access_errors_as_404() -> Iterator[None]:
    """Answer a refused lookup with 404, so "not yours" and "no such row" look alike."""
    try:
        yield
    except AccessError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, RESOURCE_NOT_ACCESSIBLE) from error


def _depends_on(dependant: Any, target: Callable[..., Any]) -> bool:
    if dependant is None:
        return False
    if dependant.call is target:
        return True
    return any(_depends_on(child, target) for child in dependant.dependencies)


def require_project_role(
    minimum: ProjectRole = ProjectRole.VIEWER,
) -> Callable[..., ProjectAccess]:
    """Build a dependency that authorises the caller on the route's ``project_id``.

    A caller without access gets 404 rather than 403 so project ids stay unguessable.
    """

    def dependency(
        project_id: str,
        session: DbSession,
        authenticated: CurrentSession,
    ) -> ProjectAccess:
        try:
            return require_project_access(
                session, authenticated.user, project_id, minimum=minimum
            )
        except ProjectAccessDenied as error:
            raise HTTPException(status.HTTP_404_NOT_FOUND, PROJECT_NOT_FOUND) from error

    return dependency


def current_user_id(authenticated: CurrentSession) -> str:
    return authenticated.user.id
