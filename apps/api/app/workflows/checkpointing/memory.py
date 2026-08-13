"""An in-process checkpoint store.

This is what the test suite and a single-process development run use, so neither needs a
MySQL server. It keeps every checkpoint of the provider's lifetime, which is enough to
exercise interrupts, resumes and redeliveries, but it dies with the process and must never
back a deployment.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from langgraph.checkpoint.memory import InMemorySaver

from app.workflows.checkpointing.base import CheckpointerInfo, CheckpointerProvider
from app.workflows.checkpointing.compatibility import installed_versions


class InMemoryCheckpointerProvider(CheckpointerProvider):
    def __init__(self) -> None:
        self._saver = InMemorySaver()

    @property
    def info(self) -> CheckpointerInfo:
        return CheckpointerInfo(
            backend="memory",
            package="langgraph-checkpoint",
            package_version=installed_versions()["langgraph-checkpoint"],
            driver=None,
            migration_level=None,
        )

    def ensure_schema(self) -> None:
        """Nothing to create: the store is a dictionary that lives as long as the process."""

    @contextmanager
    def checkpointer(self) -> Iterator[InMemorySaver]:
        yield self._saver
