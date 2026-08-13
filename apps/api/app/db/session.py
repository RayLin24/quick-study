from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.settings import Settings, get_settings


def build_engine(
    url: str | None = None,
    *,
    settings: Settings | None = None,
    **options: Any,
) -> Engine:
    """Create an engine for ``url``, defaulting to the configured MySQL database.

    ``pool_pre_ping`` matters for a long-lived worker: MySQL closes idle connections and a
    stale one would otherwise surface as a random task failure.
    """
    resolved = url or (settings or get_settings()).sqlalchemy_url
    return sa.create_engine(
        resolved,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
        **options,
    )


@lru_cache
def get_engine() -> Engine:
    return build_engine()


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Run a unit of work that commits on success and rolls back on any exception."""
    with get_session_factory()() as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Routes commit explicitly so read paths stay read-only."""
    with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            session.rollback()
            raise
