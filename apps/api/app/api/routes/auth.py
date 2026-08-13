"""Authentication endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Response, status

from app.auth.accounts import authenticate_user, create_user, needs_bootstrap
from app.auth.dependencies import CurrentSession, DbSession, require_csrf
from app.auth.sessions import (
    clear_session_cookie,
    create_session,
    revoke_session,
    set_session_cookie,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap_admin(
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: DbSession,
    display_name: Annotated[str, Form()] = "",
) -> dict[str, str]:
    """Create the first administrator. Only works when no user exists."""
    if not needs_bootstrap(session):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="already initialized")
    user = create_user(session, email=email, password=password, display_name=display_name or None)
    session.commit()
    return {"id": user.id, "email": user.email}


@router.post("/login")
def login(
    response: Response,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    session: DbSession,
) -> dict[str, str]:
    """Authenticate and set the session cookie."""
    user = authenticate_user(session, email=email, password=password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    issued = create_session(session, user)
    session.commit()
    set_session_cookie(response, issued.token)
    return {"id": user.id, "email": user.email, "csrf_token": issued.csrf_token}


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(
    response: Response,
    current: CurrentSession,
    session: DbSession,
) -> dict[str, str]:
    """Invalidate the current session."""
    revoke_session(session, current.record)
    session.commit()
    clear_session_cookie(response)
    return {"status": "ok"}


@router.get("/me")
def me(current: CurrentSession) -> dict[str, str | None]:
    """Return the authenticated user."""
    return {
        "id": current.user.id,
        "email": current.user.email,
        "display_name": current.user.display_name,
        "role": current.user.role.value,
    }
