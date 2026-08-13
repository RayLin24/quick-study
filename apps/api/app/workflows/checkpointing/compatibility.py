"""The version window this adapter has actually been tested against.

LangGraph ships a first-party PostgreSQL checkpointer; MySQL is covered by the community
package ``langgraph-checkpoint-mysql``, which tracks the official implementation by hand.
A silent upgrade of any of the three packages could therefore change the on-disk schema or
the write path without anyone noticing, so the window is declared here and checked before
the adapter touches a database. Widening it means re-running the contract tests.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from importlib.metadata import PackageNotFoundError, version
from typing import Final

#: Package name mapped to ``(minimum inclusive, maximum exclusive)``. The community MySQL
#: package is held to a single minor line because it is a hand-maintained port of the
#: PostgreSQL implementation: any release of it can move the schema.
SUPPORTED_PACKAGE_VERSIONS: Final[dict[str, tuple[str, str]]] = {
    "langgraph": ("1.2.11", "2.0.0"),
    "langgraph-checkpoint": ("4.2.0", "5.0.0"),
    "langgraph-checkpoint-mysql": ("3.0.0", "3.1.0"),
}

#: The highest ``checkpoint_migrations.v`` the pinned community package applies. The
#: contract tests assert both that a fresh database reaches it and that the package still
#: ships exactly this many migrations, so an upgrade that adds one cannot pass unnoticed.
CHECKPOINTER_MIGRATION_LEVEL: Final = 21

#: The package keeps its queries close to the PostgreSQL ones, which needs 8.0.19.
MINIMUM_MYSQL_SERVER_VERSION: Final[tuple[int, int, int]] = (8, 0, 19)

#: MySQL 9.6 removed ``MD5`` from generated column expressions and the package has no
#: migration for that yet, so the adapter refuses to run against it rather than corrupting
#: the primary key of every checkpoint table.
MAXIMUM_MYSQL_SERVER_VERSION: Final[tuple[int, int, int]] = (9, 6, 0)

_VERSION_PREFIX: Final = re.compile(r"\A\d+(?:\.\d+)*")


class UnsupportedCheckpointerBackend(RuntimeError):
    """Raised when the checkpoint store is not one this adapter was tested against."""


def parse_version(text: str) -> tuple[int, ...]:
    """Return the leading numeric components of a version string.

    MySQL decorates its version with a build suffix (``8.4.8-log``) and Python packages
    add pre-release markers; both are ignored because neither changes the contract.
    """
    match = _VERSION_PREFIX.match(text.strip())
    if match is None:
        raise UnsupportedCheckpointerBackend(f"cannot read a version out of {text!r}")
    return tuple(int(part) for part in match.group().split("."))


def installed_versions() -> dict[str, str]:
    """Return the installed version of every package the adapter depends on."""
    found: dict[str, str] = {}
    for package in SUPPORTED_PACKAGE_VERSIONS:
        try:
            found[package] = version(package)
        except PackageNotFoundError as error:
            raise UnsupportedCheckpointerBackend(f"{package} is not installed") from error
    return found


def verify_package_versions(versions: Mapping[str, str] | None = None) -> None:
    """Refuse to start when a package drifted outside the window that was tested."""
    for package, found in (versions or installed_versions()).items():
        window = SUPPORTED_PACKAGE_VERSIONS.get(package)
        if window is None:
            continue
        minimum, maximum = window
        if not parse_version(minimum) <= parse_version(found) < parse_version(maximum):
            raise UnsupportedCheckpointerBackend(
                f"{package} {found} is outside the tested range "
                f">={minimum},<{maximum}; re-run the checkpointer contract tests "
                "before widening it"
            )


def verify_server_version(reported: str) -> tuple[int, ...]:
    """Refuse a MySQL server the community checkpointer cannot be trusted on."""
    server = parse_version(reported)
    if server < MINIMUM_MYSQL_SERVER_VERSION:
        raise UnsupportedCheckpointerBackend(
            f"the MySQL checkpointer needs server {_render(MINIMUM_MYSQL_SERVER_VERSION)} "
            f"or newer, found {reported}"
        )
    if server >= MAXIMUM_MYSQL_SERVER_VERSION:
        raise UnsupportedCheckpointerBackend(
            f"server {reported} is {_render(MAXIMUM_MYSQL_SERVER_VERSION)} or newer, which "
            "dropped MD5 from generated column expressions; the pinned checkpointer has no "
            "migration for that"
        )
    return server


def _render(parts: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in parts)
