"""index the full-text columns with the ngram parser

MySQL's built-in FULLTEXT parser splits on non-word characters. Chinese prose contains
none, so a whole sentence became a single token and no query could ever match it, while
``projects.output_language`` defaults to ``zh``: the default configuration shipped with no
working retrieval at all. The ngram parser indexes overlapping n-grams of
``ngram_token_size`` characters (2 by default) instead, which makes Chinese searchable and
leaves Latin-script prose and code identifiers searchable as before.

The trade-off this buys, and the limit that remains: there is still no word segmentation,
so a Chinese query matches a contiguous run of characters. Searching for a phrase whose
words the document writes in a different order will not find it.

Rebuilding a FULLTEXT index is the only way to change its parser, so each index is dropped
and recreated. Other dialects have no pluggable parser and keep the plain indexes revision
0001 created for them.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-13 00:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TO_NGRAM: Final[tuple[str, ...]] = (
    "DROP INDEX ft_documents_title ON documents",
    "CREATE FULLTEXT INDEX ft_documents_title ON documents (title) WITH PARSER ngram",
    "DROP INDEX ft_documents_body_text ON documents",
    "CREATE FULLTEXT INDEX ft_documents_body_text ON documents (body_text) WITH PARSER ngram",
    "DROP INDEX ft_chunks_text ON chunks",
    "CREATE FULLTEXT INDEX ft_chunks_text ON chunks (text) WITH PARSER ngram",
    "DROP INDEX ft_symbols_identifier ON symbols",
    "CREATE FULLTEXT INDEX ft_symbols_identifier ON symbols"
    " (name, qualified_name, signature, docstring) WITH PARSER ngram",
)

_TO_BUILT_IN: Final[tuple[str, ...]] = (
    "DROP INDEX ft_symbols_identifier ON symbols",
    "CREATE FULLTEXT INDEX ft_symbols_identifier ON symbols"
    " (name, qualified_name, signature, docstring)",
    "DROP INDEX ft_chunks_text ON chunks",
    "CREATE FULLTEXT INDEX ft_chunks_text ON chunks (text)",
    "DROP INDEX ft_documents_body_text ON documents",
    "CREATE FULLTEXT INDEX ft_documents_body_text ON documents (body_text)",
    "DROP INDEX ft_documents_title ON documents",
    "CREATE FULLTEXT INDEX ft_documents_title ON documents (title)",
)


def upgrade() -> None:
    _run(_TO_NGRAM)


def downgrade() -> None:
    _run(_TO_BUILT_IN)


def _run(statements: tuple[str, ...]) -> None:
    if op.get_bind().dialect.name != "mysql":
        return
    for statement in statements:
        op.execute(statement)
