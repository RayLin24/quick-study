from __future__ import annotations

from logging.config import fileConfig
from typing import Any

from sqlalchemy import Connection, engine_from_config, pool

import app.db.models  # noqa: F401  importing registers every table on the metadata
from alembic import context
from app.db.base import Base
from app.db.types import StrEnumType, UtcDateTime
from app.settings import get_settings

config = context.config

if config.config_file_name and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Resolve the target database from ``-x url=...``, then the ini, then the settings."""
    override = context.get_x_argument(as_dictionary=True).get("url")
    return override or config.get_main_option("sqlalchemy.url") or get_settings().sqlalchemy_url


def render_item(item_type: str, obj: Any, autogen_context: Any) -> str | bool:
    """Render the project's column types as plain SQLAlchemy in generated revisions.

    A revision must keep working after the application types change, so it may not import
    them. Emitting the equivalent portable DDL keeps every revision a frozen snapshot.
    """
    if item_type != "type":
        return False
    if isinstance(obj, UtcDateTime):
        autogen_context.imports.add("from sqlalchemy.dialects import mysql")
        return "sa.DateTime().with_variant(mysql.DATETIME(fsp=6), 'mysql')"
    if isinstance(obj, StrEnumType):
        return f"sa.String(length={obj.impl.length})"
    return False


def run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_item=render_item,
        # SQLite cannot ALTER most things in place; batch mode rewrites the table instead.
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_item=render_item,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    existing_connection = config.attributes.get("connection")
    if existing_connection is not None:
        run_migrations(existing_connection)
        return

    section = dict(config.get_section(config.config_ini_section) or {})
    section["sqlalchemy.url"] = database_url()
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    try:
        with connectable.connect() as connection:
            run_migrations(connection)
    finally:
        connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
