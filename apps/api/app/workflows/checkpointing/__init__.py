"""The LangGraph checkpointer adapter.

LangGraph's production checkpointer is the PostgreSQL one. This deployment runs MySQL, so
the store behind the graph is the community ``langgraph-checkpoint-mysql`` package. That
package is pinned to a tested version window, reached only through
``CheckpointerProvider`` and covered by ``tests/test_checkpointer_contract.py``; swapping
it for another store means writing one provider and passing the same tests.

Checkpoints are execution state only. Which runs exist, what phase they are in and what a
reviewer approved live in MySQL's own tables, so losing the checkpoint schema costs
progress, never facts.
"""

from app.workflows.checkpointing.base import CheckpointerInfo, CheckpointerProvider
from app.workflows.checkpointing.compatibility import (
    CHECKPOINTER_MIGRATION_LEVEL,
    MAXIMUM_MYSQL_SERVER_VERSION,
    MINIMUM_MYSQL_SERVER_VERSION,
    SUPPORTED_PACKAGE_VERSIONS,
    UnsupportedCheckpointerBackend,
    installed_versions,
    verify_package_versions,
    verify_server_version,
)
from app.workflows.checkpointing.factory import build_checkpointer_provider
from app.workflows.checkpointing.memory import InMemoryCheckpointerProvider
from app.workflows.checkpointing.mysql import MySQLCheckpointerProvider

__all__ = [
    "CHECKPOINTER_MIGRATION_LEVEL",
    "MAXIMUM_MYSQL_SERVER_VERSION",
    "MINIMUM_MYSQL_SERVER_VERSION",
    "SUPPORTED_PACKAGE_VERSIONS",
    "CheckpointerInfo",
    "CheckpointerProvider",
    "InMemoryCheckpointerProvider",
    "MySQLCheckpointerProvider",
    "UnsupportedCheckpointerBackend",
    "build_checkpointer_provider",
    "installed_versions",
    "verify_package_versions",
    "verify_server_version",
]
