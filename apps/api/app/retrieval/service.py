from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Protocol, runtime_checkable

#: Shortest Latin-script term worth searching. One and two letter words carry no signal and
#: match almost everything, so a query made only of them would be misleading rather than
#: empty.
MIN_TERM_LENGTH: Final = 3

#: MySQL's ``ngram_token_size`` default. The ngram parser indexes Chinese in overlapping
#: pairs, so two characters really are a searchable word and must not be dropped.
MIN_CJK_TERM_LENGTH: Final = 2

#: Keeps an evidence pack readable and bounded when it is assembled from many hits.
EXCERPT_MAX_LENGTH: Final = 400

DEFAULT_LIMIT: Final = 20

_WORD_PATTERN: Final = re.compile(r"[\w]+", re.UNICODE)

#: CJK ideographs plus the kana blocks, which the ngram parser indexes the same way.
_CJK_PATTERN: Final = re.compile(
    r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", re.UNICODE
)


class SearchKind(StrEnum):
    """Which index a hit came from. Each maps to one MySQL FULLTEXT index."""

    DOCUMENT_TITLE = "document_title"
    DOCUMENT_BODY = "document_body"
    DOCUMENT_CHUNK = "document_chunk"
    CODE_SYMBOL = "code_symbol"


DEFAULT_KINDS: Final[tuple[SearchKind, ...]] = tuple(SearchKind)


@dataclass(frozen=True, slots=True)
class SearchWeights:
    """Relative importance of each index when hits from all of them are merged.

    Scores from different MySQL FULLTEXT indexes are not on a common scale, so these are
    a deliberate editorial choice rather than a calibration: a term in a title says more
    about what a page is about than the same term buried in its body.
    """

    document_title: float = 3.0
    document_body: float = 1.0
    document_chunk: float = 1.5
    code_symbol: float = 2.0

    def for_kind(self, kind: SearchKind) -> float:
        return {
            SearchKind.DOCUMENT_TITLE: self.document_title,
            SearchKind.DOCUMENT_BODY: self.document_body,
            SearchKind.DOCUMENT_CHUNK: self.document_chunk,
            SearchKind.CODE_SYMBOL: self.code_symbol,
        }[kind]


DEFAULT_WEIGHTS: Final = SearchWeights()


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """What to retrieve. Stable on purpose: workflow nodes should not change when the
    backing index gains a vector stage."""

    project_id: str
    text: str
    snapshot_ids: tuple[str, ...] = ()
    kinds: tuple[SearchKind, ...] = DEFAULT_KINDS
    limit: int = DEFAULT_LIMIT


@dataclass(frozen=True, slots=True)
class SearchHit:
    """One retrieved piece of evidence, already carrying what a citation needs."""

    kind: SearchKind
    score: float
    project_id: str
    snapshot_id: str
    locator: str
    title: str
    excerpt: str
    document_id: str | None = None
    chunk_id: str | None = None
    symbol_id: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)


@runtime_checkable
class SearchService(Protocol):
    """The only retrieval surface workflow nodes are allowed to depend on.

    A later hybrid implementation (MySQL FULLTEXT plus embeddings) can be dropped in
    behind this method without touching its callers.
    """

    def search(self, query: SearchQuery) -> list[SearchHit]: ...


def normalise_terms(text: str) -> tuple[str, ...]:
    """Extract searchable terms, discarding full-text operators and unusable tokens.

    Query text is untrusted: raw ``+``, ``-``, ``*``, ``(``, ``"`` and ``@`` are boolean
    full-text operators and unbalanced ones make MySQL reject the whole query. Keeping
    only word characters removes that class of failure entirely.

    Chinese is written without spaces, so a run of characters arrives as one term and is
    searched as one: there is no segmenter here, and a term matches a contiguous run in the
    text rather than a bag of words.
    """
    terms = (match.group(0) for match in _WORD_PATTERN.finditer(text))
    seen: dict[str, None] = {}
    for term in terms:
        if len(term) >= _minimum_length(term):
            seen.setdefault(term, None)
    return tuple(seen)


def _minimum_length(term: str) -> int:
    return MIN_CJK_TERM_LENGTH if _CJK_PATTERN.search(term) else MIN_TERM_LENGTH


def build_excerpt(text: str, *, limit: int = EXCERPT_MAX_LENGTH) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return f"{collapsed[: limit - 1].rstrip()}\N{HORIZONTAL ELLIPSIS}"
