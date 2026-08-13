"""The interface the workflow uses to reach a LangGraph checkpoint store.

Nothing outside this package may import a checkpointer implementation directly. The graph
only ever sees a ``CheckpointerProvider``, which owns schema creation and the lifetime of
whatever connection the backend needs, so replacing the store is a change to one module
plus a re-run of the contract tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from dataclasses import dataclass

from langgraph.checkpoint.base import BaseCheckpointSaver


@dataclass(frozen=True, slots=True)
class CheckpointerInfo:
    """What is actually storing the checkpoints, for logs, health checks and support."""

    backend: str
    package: str
    package_version: str
    driver: str | None = None
    migration_level: int | None = None


class CheckpointerProvider(ABC):
    """Creates checkpointers and owns their schema."""

    @property
    @abstractmethod
    def info(self) -> CheckpointerInfo:
        """Describe the backend, including its live migration level where it has one."""

    @abstractmethod
    def ensure_schema(self) -> None:
        """Create or migrate the checkpoint tables. Safe to call on every startup."""

    @abstractmethod
    def checkpointer(self) -> AbstractContextManager[BaseCheckpointSaver]:
        """Yield a checkpointer for one unit of work, releasing its resources after."""
