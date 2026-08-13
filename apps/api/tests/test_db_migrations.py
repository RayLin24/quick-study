"""Migration checks.

The SQLite runs prove the revision is applicable and reversible and that it matches the
models; the MySQL runs additionally prove the MySQL-only DDL (row sizes, index key
lengths, FULLTEXT) is real. MySQL checks skip unless ``QUICKSTUDY_TEST_MYSQL_URL`` is set.
"""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import alembic_config, reset_mysql_database
from sqlalchemy.orm import Session
from test_db_schema import EXPECTED_FULLTEXT_INDEXES, EXPECTED_TABLES

from alembic import command
from app.db.base import Base
from app.db.models import Chunk, Document, Project, Snapshot, Source, Symbol, User
from app.db.models.enums import CodeLanguage, DocumentKind, SourceKind, SymbolKind


def sqlite_url(tmp_path: Path) -> str:
    return f"sqlite+pysqlite:///{(tmp_path / 'domain.db').as_posix()}"


def table_names(url: str) -> set[str]:
    engine = sa.create_engine(url)
    try:
        return set(sa.inspect(engine).get_table_names())
    finally:
        engine.dispose()


def metadata_differences(url: str) -> list[object]:
    engine = sa.create_engine(url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            return list(compare_metadata(context, Base.metadata))
    finally:
        engine.dispose()


def test_upgrade_head_creates_every_domain_table(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path)

    command.upgrade(alembic_config(url), "head")

    names = table_names(url)
    assert EXPECTED_TABLES <= names
    assert "alembic_version" in names


def test_migrated_schema_matches_the_models(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path)
    command.upgrade(alembic_config(url), "head")

    assert metadata_differences(url) == []


def test_downgrade_base_removes_every_domain_table(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "head")

    command.downgrade(config, "base")

    assert table_names(url) & EXPECTED_TABLES == set()


def test_upgrade_is_idempotent_when_already_at_head(tmp_path: Path) -> None:
    url = sqlite_url(tmp_path)
    config = alembic_config(url)
    command.upgrade(config, "head")

    command.upgrade(config, "head")

    assert metadata_differences(url) == []


def test_offline_mode_emits_reviewable_sql_without_touching_a_database(tmp_path: Path) -> None:
    config = alembic_config(sqlite_url(tmp_path))
    buffer = io.StringIO()

    with redirect_stdout(buffer):
        command.upgrade(config, "head", sql=True)

    emitted = buffer.getvalue()
    assert "CREATE TABLE users" in emitted
    assert not (tmp_path / "domain.db").exists()


def test_mysql_upgrade_head_applies_the_real_production_ddl(mysql_url: str) -> None:
    url = reset_mysql_database(mysql_url)

    command.upgrade(alembic_config(url), "head")

    assert EXPECTED_TABLES <= table_names(url)


def test_mysql_migrated_schema_matches_the_models(mysql_url: str) -> None:
    url = reset_mysql_database(mysql_url)
    command.upgrade(alembic_config(url), "head")

    assert metadata_differences(url) == []


def test_mysql_creates_real_fulltext_indexes(mysql_url: str) -> None:
    url = reset_mysql_database(mysql_url)
    command.upgrade(alembic_config(url), "head")
    engine = sa.create_engine(url)

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT INDEX_NAME, TABLE_NAME, COLUMN_NAME FROM information_schema.STATISTICS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND INDEX_TYPE = 'FULLTEXT'"
                    " ORDER BY INDEX_NAME, SEQ_IN_INDEX"
                )
            ).all()
    finally:
        engine.dispose()

    observed: dict[str, tuple[str, tuple[str, ...]]] = {}
    for index_name, table_name, column_name in rows:
        _, columns = observed.setdefault(index_name, (table_name, ()))
        observed[index_name] = (table_name, (*columns, column_name))

    assert observed == EXPECTED_FULLTEXT_INDEXES


def test_mysql_parses_natural_language_indexes_as_ngrams(mysql_url: str) -> None:
    """The built-in parser never splits Chinese, and Chinese is the default output."""
    url = reset_mysql_database(mysql_url)
    command.upgrade(alembic_config(url), "head")
    engine = sa.create_engine(url)

    try:
        with engine.connect() as connection:
            definitions = {
                table: connection.execute(
                    sa.text(f"SHOW CREATE TABLE {table}")  # noqa: S608
                ).one()[1]
                for table in ("documents", "chunks", "symbols")
            }
    finally:
        engine.dispose()

    for index_name, (table, _) in EXPECTED_FULLTEXT_INDEXES.items():
        declaration = next(
            line for line in definitions[table].splitlines() if f"`{index_name}`" in line
        )
        assert "WITH PARSER `ngram`" in declaration


def test_mysql_tables_are_innodb_with_utf8mb4(mysql_url: str) -> None:
    url = reset_mysql_database(mysql_url)
    command.upgrade(alembic_config(url), "head")
    engine = sa.create_engine(url)

    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT TABLE_NAME, ENGINE, TABLE_COLLATION FROM information_schema.TABLES"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME <> 'alembic_version'"
                )
            ).all()
    finally:
        engine.dispose()

    assert {name for name, _, _ in rows} == EXPECTED_TABLES
    assert {engine_name for _, engine_name, _ in rows} == {"InnoDB"}
    assert all(collation.startswith("utf8mb4") for _, _, collation in rows)


def test_mysql_stores_rows_that_fill_every_wide_column(mysql_url: str) -> None:
    """InnoDB caps a row at roughly 8 KB, so the widest tables need a real insert.

    Every bounded string is filled with a four-byte character, which is the worst case
    utf8mb4 has to survive.
    """
    url = reset_mysql_database(mysql_url)
    command.upgrade(alembic_config(url), "head")
    engine = sa.create_engine(url)
    wide = "\N{ROCKET}"
    long_body = "deployment gateway supervisor " * 40_000

    try:
        with Session(engine) as session:
            user = User(
                email=f"{'a' * 300}@example.test",
                display_name=wide * 120,
                password_hash="h" * 255,
            )
            session.add(user)
            session.flush()
            project = Project(owner_id=user.id, slug=wide * 96, name=wide * 160)
            session.add(project)
            session.flush()
            source = Source(
                project_id=project.id,
                kind=SourceKind.WEBSITE,
                locator=f"https://example.test/{'u' * 1000}",
                locator_fingerprint="a" * 64,
                display_name=wide * 255,
            )
            session.add(source)
            session.flush()
            snapshot = Snapshot(project_id=project.id, source_id=source.id, fingerprint="b" * 64)
            session.add(snapshot)
            session.flush()
            document = Document(
                project_id=project.id,
                snapshot_id=snapshot.id,
                source_id=source.id,
                kind=DocumentKind.WEB_PAGE,
                uri=f"https://example.test/{'u' * 1000}",
                uri_fingerprint="c" * 64,
                path=wide * 768,
                title=wide * 512,
                body_text=long_body,
            )
            session.add(document)
            session.flush()
            session.add(
                Chunk(
                    project_id=project.id,
                    document_id=document.id,
                    ordinal=0,
                    heading_path=wide * 768,
                    text=long_body,
                    sha256="d" * 64,
                )
            )
            session.add(
                Symbol(
                    project_id=project.id,
                    document_id=document.id,
                    kind=SymbolKind.FUNCTION,
                    language=CodeLanguage.PYTHON,
                    name=wide * 255,
                    qualified_name=wide * 512,
                    signature=wide * 1024,
                    docstring="explanatory prose " * 40_000,
                )
            )
            session.commit()

            reloaded = session.get(Document, document.id)
            assert reloaded is not None
            assert len(reloaded.title) == 512
            assert len(reloaded.body_text) == len(long_body)
    finally:
        engine.dispose()
