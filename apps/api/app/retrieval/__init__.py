"""Evidence retrieval.

``service`` defines the stable surface every workflow node uses; ``fulltext`` implements
it on MySQL FULLTEXT today. A hybrid keyword-plus-vector implementation can replace the
implementation without changing a single caller.
"""

from app.retrieval.fulltext import (
    MySQLFullTextSearchService,
    PortableLikeSearchService,
    SearchBackend,
    UnsupportedSearchBackend,
    build_search_service,
)
from app.retrieval.service import (
    DEFAULT_KINDS,
    DEFAULT_LIMIT,
    DEFAULT_WEIGHTS,
    EXCERPT_MAX_LENGTH,
    MIN_CJK_TERM_LENGTH,
    MIN_TERM_LENGTH,
    SearchHit,
    SearchKind,
    SearchQuery,
    SearchService,
    SearchWeights,
    normalise_terms,
)

__all__ = [
    "DEFAULT_KINDS",
    "DEFAULT_LIMIT",
    "DEFAULT_WEIGHTS",
    "EXCERPT_MAX_LENGTH",
    "MIN_CJK_TERM_LENGTH",
    "MIN_TERM_LENGTH",
    "MySQLFullTextSearchService",
    "PortableLikeSearchService",
    "SearchBackend",
    "SearchHit",
    "SearchKind",
    "SearchQuery",
    "SearchService",
    "SearchWeights",
    "UnsupportedSearchBackend",
    "build_search_service",
    "normalise_terms",
]
