"""The MySQL checkpoint store, wrapping the community ``langgraph-checkpoint-mysql``.

Everything specific to that package is confined to this module: the connection settings it
insists on, the schema it owns, the server versions it works on and the class that
implements it. The rest of the system only sees ``CheckpointerProvider``.

Two of its requirements are not optional and are enforced here rather than documented:
connections must be in autocommit mode, or ``setup()`` silently fails to persist the
tables, and the server must be at least MySQL 8.0.19, because the queries were ported from
the PostgreSQL implementation.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from typing import Any, Final

import pymysql
import sqlalchemy as sa
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

from app.settings import Settings, get_settings
from app.workflows.checkpointing.base import CheckpointerInfo, CheckpointerProvider
from app.workflows.checkpointing.compatibility import (
    installed_versions,
    verify_package_versions,
    verify_server_version,
)

PACKAGE: Final = "langgraph-checkpoint-mysql"
DEFAULT_CONNECT_TIMEOUT: Final = 10


class MySQLCheckpointerProvider(CheckpointerProvider):
    """Hands out ``PyMySQLSaver`` instances bound to short-lived connections.

    A connection per unit of work is what a Celery worker wants: tasks are minutes apart,
    MySQL closes idle connections, and a saver that outlived its connection would fail on
    a checkpoint write rather than at claim time.
    """

    def __init__(
        self,
        url: str,
        *,
        saver_class: type[PyMySQLSaver] = PyMySQLSaver,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self._url = url
        self._saver_class = saver_class
        self._connect_arguments = _connect_arguments(url, connect_timeout)

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> MySQLCheckpointerProvider:
        resolved = settings or get_settings()
        return cls(resolved.checkpointer_url or resolved.sqlalchemy_url)

    @property
    def url(self) -> str:
        return self._url

    @property
    def info(self) -> CheckpointerInfo:
        return CheckpointerInfo(
            backend="mysql",
            package=PACKAGE,
            package_version=installed_versions()[PACKAGE],
            driver="pymysql",
            migration_level=self.migration_level(),
        )

    def connect(self) -> pymysql.connections.Connection:
        """Open a checkpointer connection. Autocommit is part of the package's contract."""
        return pymysql.connect(**self._connect_arguments, autocommit=True)

    def ensure_schema(self) -> None:
        verify_package_versions()
        with closing(self.connect()) as connection:
            verify_server_version(_server_version(connection))
            self._saver_class(connection).setup()

    @contextmanager
    def checkpointer(self) -> Iterator[PyMySQLSaver]:
        with closing(self.connect()) as connection:
            yield self._saver_class(connection)

    def server_version(self) -> tuple[int, ...]:
        with closing(self.connect()) as connection:
            return verify_server_version(_server_version(connection))

    def migration_level(self) -> int | None:
        """Return the highest applied migration, or ``None`` before the schema exists."""
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            try:
                cursor.execute("SELECT MAX(v) FROM checkpoint_migrations")
            except pymysql.err.ProgrammingError:
                return None
            row = cursor.fetchone()
        return None if row is None else row[0]


def _connect_arguments(url: str, connect_timeout: int) -> dict[str, Any]:
    """Translate the deployment's SQLAlchemy URL into PyMySQL keyword arguments."""
    parsed = sa.make_url(url)
    if not parsed.database:
        raise ValueError(f"the checkpointer URL must name a database: {url!r}")
    return {
        "host": parsed.host or "127.0.0.1",
        "port": parsed.port or 3306,
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": parsed.database,
        "charset": "utf8mb4",
        "connect_timeout": connect_timeout,
    }


def _server_version(connection: pymysql.connections.Connection) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT VERSION()")
        row = cursor.fetchone()
    return "" if row is None else str(row[0])
