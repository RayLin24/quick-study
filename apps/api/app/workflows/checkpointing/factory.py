"""Choosing a checkpoint store from configuration."""

from __future__ import annotations

from app.settings import Settings, get_settings
from app.workflows.checkpointing.base import CheckpointerProvider
from app.workflows.checkpointing.compatibility import UnsupportedCheckpointerBackend
from app.workflows.checkpointing.memory import InMemoryCheckpointerProvider
from app.workflows.checkpointing.mysql import MySQLCheckpointerProvider


def build_checkpointer_provider(
    *,
    backend: str | None = None,
    url: str | None = None,
    settings: Settings | None = None,
) -> CheckpointerProvider:
    """Return the configured provider.

    ``memory`` exists for tests and single-process development runs; a deployment always
    uses ``mysql`` so a restarted worker can pick a run back up.
    """
    resolved = settings or get_settings()
    chosen = backend or resolved.checkpointer_backend
    if chosen == "memory":
        return InMemoryCheckpointerProvider()
    if chosen == "mysql":
        return MySQLCheckpointerProvider(
            url or resolved.checkpointer_url or resolved.sqlalchemy_url
        )
    raise UnsupportedCheckpointerBackend(
        f"{chosen!r} is not a checkpoint store this deployment knows; "
        "the tested backends are 'mysql' and 'memory'"
    )
