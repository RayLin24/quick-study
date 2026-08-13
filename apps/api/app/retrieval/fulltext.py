"""Keyword retrieval over the indexed corpus.

The production implementation uses MySQL ``MATCH ... AGAINST`` in boolean mode over the
four FULLTEXT indexes the migrations create. Boolean mode is deliberate: natural-language
mode silently ignores any term that appears in more than half the rows, which is exactly
what happens to the vocabulary of a single product's documentation.

Revision ``0002`` builds those indexes ``WITH PARSER ngram``, which is what makes Chinese
searchable: the built-in parser splits on non-word characters and Chinese prose has none,
so a whole sentence became one token. The limit that remains is word segmentation -- a
Chinese term matches a contiguous run of characters, so a query that writes the same words
in a different order will not find the document.

:class:`PortableLikeSearchService` is a substring-matching stand-in for sessions that are
not bound to MySQL, so the interface can be exercised without a MySQL server. Its ranking
is a match count, not relevance; it is not a production search backend, and
:func:`build_search_service` never selects it unless it is asked for by name.
"""

from __future__ import annotations

import functools
import operator
from collections.abc import Sequence
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.mysql import match as mysql_match
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, Symbol
from app.retrieval.service import (
    DEFAULT_WEIGHTS,
    SearchHit,
    SearchKind,
    SearchQuery,
    SearchWeights,
    build_excerpt,
    normalise_terms,
)

_LIKE_ESCAPE = "\\"


class SearchBackend(StrEnum):
    """Which retrieval implementation to build."""

    #: MySQL FULLTEXT in boolean mode. The only one that is a search engine.
    MYSQL_FULLTEXT = "mysql_fulltext"
    #: A substring scan. For tests and for exercising the interface without MySQL.
    PORTABLE_LIKE = "portable_like"


class UnsupportedSearchBackend(Exception):
    """Raised when a session's database has no production retrieval implementation."""


class _CorpusSearchService:
    """Shared query shapes. Subclasses supply the relevance and matching expressions."""

    def __init__(self, session: Session, *, weights: SearchWeights = DEFAULT_WEIGHTS) -> None:
        self._session = session
        self._weights = weights

    def search(self, query: SearchQuery) -> list[SearchHit]:
        terms = normalise_terms(query.text)
        if not terms or query.limit <= 0:
            return []
        hits: list[SearchHit] = []
        for kind in dict.fromkeys(query.kinds):
            hits.extend(self._search_one_index(kind, query, terms))
        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[: query.limit]

    def _relevance(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        raise NotImplementedError

    def _matches(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        raise NotImplementedError

    def _search_one_index(
        self,
        kind: SearchKind,
        query: SearchQuery,
        terms: Sequence[str],
    ) -> list[SearchHit]:
        if kind is SearchKind.DOCUMENT_TITLE:
            return self._document_hits(kind, [Document.title], query, terms)
        if kind is SearchKind.DOCUMENT_BODY:
            return self._document_hits(kind, [Document.body_text], query, terms)
        if kind is SearchKind.DOCUMENT_CHUNK:
            return self._chunk_hits(query, terms)
        return self._symbol_hits(query, terms)

    def _document_hits(
        self,
        kind: SearchKind,
        columns: Sequence[Any],
        query: SearchQuery,
        terms: Sequence[str],
    ) -> list[SearchHit]:
        score = self._relevance(columns, terms).label("score")
        statement = (
            sa.select(
                Document.id,
                Document.project_id,
                Document.snapshot_id,
                Document.uri,
                Document.title,
                Document.body_text,
                score,
            )
            .where(
                Document.project_id == query.project_id,
                self._matches(columns, terms),
                *self._snapshot_filter(Document.snapshot_id, query),
            )
            .order_by(score.desc(), Document.id)
            .limit(query.limit)
        )
        weight = self._weights.for_kind(kind)
        return [
            SearchHit(
                kind=kind,
                score=float(row.score) * weight,
                project_id=row.project_id,
                snapshot_id=row.snapshot_id,
                locator=row.uri,
                title=row.title,
                excerpt=build_excerpt(
                    row.title if kind is SearchKind.DOCUMENT_TITLE else row.body_text
                ),
                document_id=row.id,
            )
            for row in self._session.execute(statement).all()
        ]

    def _chunk_hits(self, query: SearchQuery, terms: Sequence[str]) -> list[SearchHit]:
        columns = [Chunk.text]
        score = self._relevance(columns, terms).label("score")
        statement = (
            sa.select(
                Chunk.id,
                Chunk.project_id,
                Chunk.document_id,
                Chunk.ordinal,
                Chunk.anchor,
                Chunk.text,
                Document.uri,
                Document.title,
                Document.snapshot_id,
                score,
            )
            .join(Document, Chunk.document_id == Document.id)
            .where(
                Chunk.project_id == query.project_id,
                self._matches(columns, terms),
                *self._snapshot_filter(Document.snapshot_id, query),
            )
            .order_by(score.desc(), Chunk.id)
            .limit(query.limit)
        )
        weight = self._weights.for_kind(SearchKind.DOCUMENT_CHUNK)
        return [
            SearchHit(
                kind=SearchKind.DOCUMENT_CHUNK,
                score=float(row.score) * weight,
                project_id=row.project_id,
                snapshot_id=row.snapshot_id,
                locator=f"{row.uri}#{row.anchor or f'chunk-{row.ordinal}'}",
                title=row.title,
                excerpt=build_excerpt(row.text),
                document_id=row.document_id,
                chunk_id=row.id,
            )
            for row in self._session.execute(statement).all()
        ]

    def _symbol_hits(self, query: SearchQuery, terms: Sequence[str]) -> list[SearchHit]:
        # The FULLTEXT index covers exactly these four columns, and MySQL requires a
        # MATCH to name the whole index.
        columns = [Symbol.name, Symbol.qualified_name, Symbol.signature, Symbol.docstring]
        score = self._relevance(columns, terms).label("score")
        statement = (
            sa.select(
                Symbol.id,
                Symbol.project_id,
                Symbol.document_id,
                Symbol.qualified_name,
                Symbol.signature,
                Symbol.start_line,
                Symbol.end_line,
                Document.snapshot_id,
                Document.path,
                score,
            )
            .join(Document, Symbol.document_id == Document.id)
            .where(
                Symbol.project_id == query.project_id,
                self._matches(columns, terms),
                *self._snapshot_filter(Document.snapshot_id, query),
            )
            .order_by(score.desc(), Symbol.id)
            .limit(query.limit)
        )
        weight = self._weights.for_kind(SearchKind.CODE_SYMBOL)
        return [
            SearchHit(
                kind=SearchKind.CODE_SYMBOL,
                score=float(row.score) * weight,
                project_id=row.project_id,
                snapshot_id=row.snapshot_id,
                locator=f"{row.path}#L{row.start_line}-L{row.end_line}",
                title=row.qualified_name,
                excerpt=build_excerpt(row.signature),
                document_id=row.document_id,
                symbol_id=row.id,
            )
            for row in self._session.execute(statement).all()
        ]

    @staticmethod
    def _snapshot_filter(column: Any, query: SearchQuery) -> list[Any]:
        """Restrict results to approved snapshots when the caller named them."""
        return [column.in_(query.snapshot_ids)] if query.snapshot_ids else []


class MySQLFullTextSearchService(_CorpusSearchService):
    """The production implementation: InnoDB FULLTEXT in boolean mode."""

    def _match_expression(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        # Bare terms mean "any of these, ranked", which keeps recall high enough for an
        # evidence pack; the trailing wildcard also matches inflected forms.
        against = " ".join(f"{term}*" for term in terms)
        return mysql_match(*columns, against=against, in_boolean_mode=True)

    def _relevance(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        return self._match_expression(columns, terms)

    def _matches(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        return self._match_expression(columns, terms)


class PortableLikeSearchService(_CorpusSearchService):
    """Substring fallback for sessions that are not bound to MySQL.

    Exists so the retrieval interface is testable and usable without a MySQL server. It
    scores by counting matching column/term pairs and does not scale.
    """

    def _relevance(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        matches = [
            sa.case((self._like(column, term), 1.0), else_=0.0)
            for column in columns
            for term in terms
        ]
        return functools.reduce(operator.add, matches)

    def _matches(self, columns: Sequence[Any], terms: Sequence[str]) -> Any:
        return sa.or_(*[self._like(column, term) for column in columns for term in terms])

    @staticmethod
    def _like(column: Any, term: str) -> Any:
        escaped = term.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        for wildcard in ("%", "_"):
            escaped = escaped.replace(wildcard, f"{_LIKE_ESCAPE}{wildcard}")
        return column.like(f"%{escaped}%", escape=_LIKE_ESCAPE)


def build_search_service(
    session: Session,
    *,
    backend: SearchBackend | None = None,
    weights: SearchWeights = DEFAULT_WEIGHTS,
) -> _CorpusSearchService:
    """Return a retrieval implementation, refusing to guess a downgrade.

    Without ``backend`` only MySQL is selected automatically. Falling back quietly to the
    substring scan would let a deployment run on something that answers like search and is
    not one, and nothing would say so; a database with no real implementation raises here
    instead. Ask for :attr:`SearchBackend.PORTABLE_LIKE` by name where that is the point.
    """
    resolved = backend or _backend_for(session)
    if resolved is SearchBackend.MYSQL_FULLTEXT:
        return MySQLFullTextSearchService(session, weights=weights)
    return PortableLikeSearchService(session, weights=weights)


def _backend_for(session: Session) -> SearchBackend:
    dialect = session.get_bind().dialect.name
    if dialect == "mysql":
        return SearchBackend.MYSQL_FULLTEXT
    raise UnsupportedSearchBackend(
        f"{dialect} has no full-text implementation; retrieval needs MySQL, or pass "
        f"backend={SearchBackend.PORTABLE_LIKE!r} to accept a substring scan"
    )
