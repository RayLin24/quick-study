"""Contract tests for the LangGraph checkpointer adapter.

LangGraph's own production checkpointer is the PostgreSQL one; MySQL is served by the
community package ``langgraph-checkpoint-mysql``. That package is the reason this adapter
exists, and this file is the reason the package can be replaced: it states what any
backend behind ``CheckpointerProvider`` has to do, and a substitute is only acceptable
once it passes the same suite.

The backend-agnostic class runs against every provider the adapter offers. The MySQL
classes additionally pin down what only a real server can show — schema creation and
migration, concurrent connections, recovery after a hard kill and a database written by
the previous release of the package. They skip unless ``QUICKSTUDY_TEST_MYSQL_URL``
points at a scratch schema.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from _checkpointer_worker import build_approval_probe, build_stall_probe
from conftest import MYSQL_URL_ENV
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver
from langgraph.types import Command

from app.workflows.checkpointing import (
    CheckpointerProvider,
    InMemoryCheckpointerProvider,
    MySQLCheckpointerProvider,
    UnsupportedCheckpointerBackend,
    build_checkpointer_provider,
)
from app.workflows.checkpointing.compatibility import (
    CHECKPOINTER_MIGRATION_LEVEL,
    MAXIMUM_MYSQL_SERVER_VERSION,
    MINIMUM_MYSQL_SERVER_VERSION,
    SUPPORTED_PACKAGE_VERSIONS,
    installed_versions,
    verify_package_versions,
    verify_server_version,
)

WORKER = Path(__file__).with_name("_checkpointer_worker.py")
API_ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT_TABLES = frozenset(
    {"checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"}
)

#: The migration level a database is left at by the releases before the community package
#: stopped deriving ``checkpoint_ns_hash`` with a stored generated column. A deployment
#: that was set up back then is the realistic "upgrade me" case.
LEGACY_SCHEMA_LEVEL = 18


def render(url: sa.URL) -> str:
    """``str(URL)`` masks the password, which would make every connection fail."""
    return url.render_as_string(hide_password=False)


def checkpointer_url() -> str:
    """A scratch schema for the checkpointer, separate from the domain test schema."""
    configured = os.environ.get(MYSQL_URL_ENV)
    if not configured:
        pytest.skip(f"set {MYSQL_URL_ENV} to run the MySQL checkpointer contract")
    url = sa.make_url(configured)
    return render(url.set(database=f"{url.database}_checkpoints"))


def recreate_schema(url: str) -> str:
    target = sa.make_url(url)
    database = target.database
    assert database and "test" in database, f"point {MYSQL_URL_ENV} at a scratch schema"
    server = sa.create_engine(target.set(database=""), isolation_level="AUTOCOMMIT")
    try:
        with server.connect() as connection:
            connection.execute(sa.text(f"DROP DATABASE IF EXISTS `{database}`"))
            connection.execute(
                sa.text(
                    f"CREATE DATABASE `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
    finally:
        server.dispose()
    return url


@pytest.fixture
def mysql_checkpointer_url() -> str:
    return recreate_schema(checkpointer_url())


@pytest.fixture
def mysql_provider(mysql_checkpointer_url: str) -> Iterator[MySQLCheckpointerProvider]:
    provider = MySQLCheckpointerProvider(mysql_checkpointer_url)
    provider.ensure_schema()
    yield provider


@pytest.fixture(params=["memory", "mysql"])
def provider(request: pytest.FixtureRequest) -> Iterator[CheckpointerProvider]:
    if request.param == "memory":
        provider: CheckpointerProvider = InMemoryCheckpointerProvider()
    else:
        provider = MySQLCheckpointerProvider(recreate_schema(checkpointer_url()))
    provider.ensure_schema()
    yield provider


def tables_in(url: str) -> set[str]:
    engine = sa.create_engine(url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def scalar(url: str, statement: str) -> Any:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            return connection.execute(sa.text(statement)).scalar()
    finally:
        engine.dispose()


def config(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


class TestBackendContract:
    """What every checkpointer backend has to guarantee."""

    def test_ensure_schema_can_be_called_repeatedly(
        self, provider: CheckpointerProvider
    ) -> None:
        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            graph.invoke({"visited": [], "approved": False}, config("t"), durability="sync")

        provider.ensure_schema()
        provider.ensure_schema()

        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            assert graph.get_state(config("t")).values["visited"] == ["first"]

    def test_a_run_stops_at_an_interrupt_and_reports_it(
        self, provider: CheckpointerProvider
    ) -> None:
        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            result = graph.invoke(
                {"visited": [], "approved": False}, config("t"), durability="sync"
            )
            assert result["__interrupt__"][0].value == {"question": "approve?"}
            assert graph.get_state(config("t")).next == ("gate",)

    def test_a_new_provider_instance_resumes_the_same_thread(
        self, provider: CheckpointerProvider
    ) -> None:
        with provider.checkpointer() as checkpointer:
            build_approval_probe().compile(checkpointer=checkpointer).invoke(
                {"visited": [], "approved": False}, config("t"), durability="sync"
            )

        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            final = graph.invoke(
                Command(resume={"approved": True}), config("t"), durability="sync"
            )

        assert final["visited"] == ["first", "gate", "last"]
        assert final["approved"] is True

    def test_a_redelivered_resume_does_not_run_the_graph_twice(
        self, provider: CheckpointerProvider, tmp_path: Path
    ) -> None:
        effects = tmp_path / "effects.txt"
        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe(effects).compile(checkpointer=checkpointer)
            graph.invoke({"visited": [], "approved": False}, config("t"), durability="sync")
            graph.invoke(Command(resume={"approved": True}), config("t"), durability="sync")
            after_first = effects.read_text(encoding="utf-8").split()

            graph.invoke(Command(resume={"approved": True}), config("t"), durability="sync")

        assert after_first == ["first", "gate", "last"]
        assert effects.read_text(encoding="utf-8").split() == after_first

    def test_threads_do_not_leak_into_each_other(self, provider: CheckpointerProvider) -> None:
        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            graph.invoke({"visited": [], "approved": False}, config("a"), durability="sync")
            graph.invoke({"visited": [], "approved": False}, config("b"), durability="sync")
            graph.invoke(Command(resume={"approved": True}), config("a"), durability="sync")

            assert graph.get_state(config("a")).next == ()
            assert graph.get_state(config("b")).next == ("gate",)

    def test_the_history_of_a_thread_is_readable(self, provider: CheckpointerProvider) -> None:
        with provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            graph.invoke({"visited": [], "approved": False}, config("t"), durability="sync")
            history = list(graph.get_state_history(config("t")))
        assert len(history) >= 2

    def test_the_provider_describes_the_backend_it_is_using(
        self, provider: CheckpointerProvider
    ) -> None:
        info = provider.info
        assert info.backend in {"memory", "mysql"}
        assert info.package
        assert info.package_version


class TestConcurrentWrites:
    def test_independent_runs_progress_in_parallel(
        self, provider: CheckpointerProvider
    ) -> None:
        def run(index: int) -> list[str]:
            with provider.checkpointer() as checkpointer:
                graph = build_approval_probe().compile(checkpointer=checkpointer)
                graph.invoke(
                    {"visited": [], "approved": False}, config(f"t{index}"), durability="sync"
                )
                final = graph.invoke(
                    Command(resume={"approved": True}), config(f"t{index}"), durability="sync"
                )
                return final["visited"]

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(8)))

        assert results == [["first", "gate", "last"]] * 8

    def test_parallel_writes_to_one_thread_all_survive(
        self, provider: CheckpointerProvider
    ) -> None:
        raw = {"configurable": {"thread_id": "shared", "checkpoint_ns": ""}}
        with provider.checkpointer() as checkpointer:
            saved = checkpointer.put(raw, empty_checkpoint(), {"source": "input"}, {})

        def write(index: int) -> None:
            with provider.checkpointer() as checkpointer:
                checkpointer.put_writes(saved, [("channel", index)], f"task-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(8)))

        with provider.checkpointer() as checkpointer:
            stored = checkpointer.get_tuple(saved)
        assert stored is not None
        assert {task_id for task_id, _, _ in stored.pending_writes} == {
            f"task-{index}" for index in range(8)
        }


class TestMySQLSchema:
    def test_ensure_schema_creates_the_checkpoint_tables(
        self, mysql_checkpointer_url: str
    ) -> None:
        assert tables_in(mysql_checkpointer_url) == set()

        MySQLCheckpointerProvider(mysql_checkpointer_url).ensure_schema()

        assert CHECKPOINT_TABLES <= tables_in(mysql_checkpointer_url)

    def test_the_migration_level_is_the_one_the_adapter_was_tested_against(
        self, mysql_provider: MySQLCheckpointerProvider
    ) -> None:
        assert mysql_provider.info.migration_level == CHECKPOINTER_MIGRATION_LEVEL

    def test_running_the_migration_again_preserves_existing_threads(
        self, mysql_provider: MySQLCheckpointerProvider
    ) -> None:
        with mysql_provider.checkpointer() as checkpointer:
            build_approval_probe().compile(checkpointer=checkpointer).invoke(
                {"visited": [], "approved": False}, config("t"), durability="sync"
            )
        before = scalar(mysql_provider.url, "SELECT COUNT(*) FROM checkpoints")

        mysql_provider.ensure_schema()

        assert scalar(mysql_provider.url, "SELECT COUNT(*) FROM checkpoints") == before
        with mysql_provider.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            final = graph.invoke(
                Command(resume={"approved": True}), config("t"), durability="sync"
            )
        assert final["visited"] == ["first", "gate", "last"]

    def test_a_database_written_by_an_older_release_is_migrated_forward(
        self, mysql_checkpointer_url: str
    ) -> None:
        """The upgrade path that makes the pinned community version replaceable.

        A schema left behind by an older release is brought to the current migration level
        by ``ensure_schema`` alone, and a thread that was waiting for a human before the
        upgrade is still waiting for the same human afterwards.
        """
        current_url = sa.make_url(mysql_checkpointer_url)
        current_database = current_url.database
        legacy_url = render(current_url.set(database=f"{current_url.database}_legacy"))
        recreate_schema(legacy_url)
        legacy_database = sa.make_url(legacy_url).database

        class OlderReleaseSaver(PyMySQLSaver):
            MIGRATIONS = PyMySQLSaver.MIGRATIONS[: LEGACY_SCHEMA_LEVEL + 1]

        legacy = MySQLCheckpointerProvider(legacy_url, saver_class=OlderReleaseSaver)
        legacy.ensure_schema()
        assert legacy.info.migration_level == LEGACY_SCHEMA_LEVEL

        provider = MySQLCheckpointerProvider(mysql_checkpointer_url)
        provider.ensure_schema()
        with provider.checkpointer() as checkpointer:
            build_approval_probe().compile(checkpointer=checkpointer).invoke(
                {"visited": [], "approved": False}, config("t"), durability="sync"
            )

        # That release derived ``checkpoint_ns_hash`` in the database, so copying every
        # other column is what a database written back then looks like.
        copies = {
            "checkpoints": (
                "thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                "checkpoint, metadata"
            ),
            "checkpoint_blobs": "thread_id, checkpoint_ns, channel, version, type, `blob`",
            "checkpoint_writes": (
                "thread_id, checkpoint_ns, checkpoint_id, task_id, task_path, idx, "
                "channel, type, `blob`"
            ),
        }
        engine = sa.create_engine(legacy_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                for table, columns in copies.items():
                    # Schema and column names cannot be bound parameters. Both come from
                    # constants in this file and from the scratch schema name the fixture
                    # built, never from anything a caller supplies.
                    connection.execute(
                        sa.text(
                            f"INSERT INTO `{legacy_database}`.{table} ({columns}) "  # noqa: S608
                            f"SELECT {columns} FROM `{current_database}`.{table}"
                        )
                    )
        finally:
            engine.dispose()

        upgraded = MySQLCheckpointerProvider(legacy_url)
        upgraded.ensure_schema()

        assert upgraded.info.migration_level == CHECKPOINTER_MIGRATION_LEVEL
        with upgraded.checkpointer() as checkpointer:
            graph = build_approval_probe().compile(checkpointer=checkpointer)
            assert graph.get_state(config("t")).next == ("gate",)
            final = graph.invoke(
                Command(resume={"approved": True}), config("t"), durability="sync"
            )
        assert final["visited"] == ["first", "gate", "last"]


class TestHardKillRecovery:
    def test_a_killed_worker_leaves_a_resumable_checkpoint(
        self, mysql_provider: MySQLCheckpointerProvider, tmp_path: Path
    ) -> None:
        marker = tmp_path / "marker.txt"
        effects = tmp_path / "effects.txt"
        effects.write_text("", encoding="utf-8")

        environment = {**os.environ, "PYTHONPATH": str(API_ROOT)}
        worker = subprocess.Popen(  # noqa: S603 - this interpreter, running a file in this directory
            [sys.executable, str(WORKER), mysql_provider.url, "killed", str(marker), str(effects)],
            env=environment,
            cwd=str(API_ROOT),
        )
        try:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline and not marker.exists():
                if worker.poll() is not None:
                    pytest.fail(f"the worker exited early with {worker.returncode}")
                time.sleep(0.1)
            assert marker.exists(), "the worker never reached the node it should die inside"
            worker.kill()
            worker.wait(timeout=60)
        finally:
            if worker.poll() is None:  # pragma: no cover - only on an assertion failure
                worker.kill()

        assert effects.read_text(encoding="utf-8").split() == ["first", "stall-entered"]

        with mysql_provider.checkpointer() as checkpointer:
            recovered = build_stall_probe().compile(checkpointer=checkpointer)
            snapshot = recovered.get_state(config("killed"))
            assert snapshot.next == ("stall",)
            assert snapshot.values["visited"] == ["first"]

        with mysql_provider.checkpointer() as checkpointer:
            graph = build_stall_probe(
                effects=effects, middle=lambda state: {"visited": ["stall"]}
            ).compile(checkpointer=checkpointer)
            final = graph.invoke(None, config("killed"), durability="sync")

        assert final["visited"] == ["first", "stall", "last"]
        assert effects.read_text(encoding="utf-8").split().count("first") == 1


class TestPinnedCompatibility:
    def test_the_installed_packages_are_inside_the_tested_window(self) -> None:
        verify_package_versions()

    def test_every_pinned_package_is_actually_installed(self) -> None:
        assert set(installed_versions()) == set(SUPPORTED_PACKAGE_VERSIONS)

    def test_an_unexpected_community_package_version_is_refused(self) -> None:
        with pytest.raises(UnsupportedCheckpointerBackend, match="langgraph-checkpoint-mysql"):
            verify_package_versions({"langgraph-checkpoint-mysql": "99.0.0"})

    def test_the_migration_count_of_the_pinned_package_has_not_changed(self) -> None:
        assert len(PyMySQLSaver.MIGRATIONS) == CHECKPOINTER_MIGRATION_LEVEL + 1

    def test_a_server_older_than_the_package_supports_is_refused(self) -> None:
        with pytest.raises(UnsupportedCheckpointerBackend, match="8.0.19"):
            verify_server_version("8.0.18")

    def test_the_supported_server_window_is_accepted(self) -> None:
        verify_server_version("8.4.8")
        verify_server_version(f"{'.'.join(str(p) for p in MINIMUM_MYSQL_SERVER_VERSION)}-log")

    def test_a_server_that_dropped_the_hash_expression_is_refused(self) -> None:
        too_new = ".".join(str(part) for part in MAXIMUM_MYSQL_SERVER_VERSION)
        with pytest.raises(UnsupportedCheckpointerBackend):
            verify_server_version(too_new)

    def test_ensure_schema_checks_the_live_server_version(
        self, mysql_checkpointer_url: str
    ) -> None:
        provider = MySQLCheckpointerProvider(mysql_checkpointer_url)
        provider.ensure_schema()
        assert provider.server_version() >= MINIMUM_MYSQL_SERVER_VERSION


class TestFactory:
    def test_the_memory_backend_is_selectable_by_name(self) -> None:
        provider = build_checkpointer_provider(backend="memory")
        assert isinstance(provider, InMemoryCheckpointerProvider)

    def test_the_mysql_backend_is_selectable_by_name(self) -> None:
        provider = build_checkpointer_provider(
            backend="mysql", url="mysql+pymysql://user:pw@127.0.0.1:3306/db"
        )
        assert isinstance(provider, MySQLCheckpointerProvider)

    def test_an_unknown_backend_is_refused(self) -> None:
        with pytest.raises(UnsupportedCheckpointerBackend, match="postgres"):
            build_checkpointer_provider(backend="postgres")
