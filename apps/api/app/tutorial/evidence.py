"""Evidence packs: what one chapter is allowed to talk about.

Built on top of :mod:`app.retrieval` without touching it. Retrieval answers "what matches
these words"; a pack answers "what may this chapter cite", which needs three more things:

* the queries a chapter implies -- its title, the questions the outline attached to it, and
  the code symbols it names, the last of those searched in the symbol index only;
* editorial weighting on top of the index score, because an exact symbol definition is worth
  more than a page that happens to mention the same identifier, and a path the outline
  pointed at is worth more than a changelog entry;
* hard limits, since a pack has to fit in a prompt and in a reviewer's attention: one page
  cannot fill the whole pack, and both an item count and a character budget apply.

Every item is resolvable to a citation before it enters the pack. A hit from a snapshot that
is not in the approved scope, or whose snapshot cannot be described as a citable location, is
dropped rather than cited vaguely.

The vector stage the plan defers plugs in at :class:`EvidenceReranker`, after the lexical
merge and before the budget is applied, so no caller changes when it arrives.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Final, Protocol

import sqlalchemy as sa
from sqlalchemy.orm import Session

from app.db.models import Snapshot, Source
from app.db.models.enums import CitationKind, SourceKind
from app.retrieval import SearchHit, SearchKind, SearchQuery, SearchService
from app.retrieval.service import normalise_terms
from app.tutorial.schema import EvidenceCitation

#: Enough evidence for a chapter to be specific, few enough for a reviewer to check.
DEFAULT_MAX_ITEMS: Final = 12

#: Characters of excerpt text a pack may carry. Roughly two thousand tokens: large enough
#: to answer a chapter's questions, small enough to leave room for instructions and output.
DEFAULT_CHAR_BUDGET: Final = 8_000

#: How many hits each derived query may contribute before merging.
DEFAULT_PER_QUERY_LIMIT: Final = 10

#: How many items one document may contribute, so a single long page cannot fill the pack.
DEFAULT_MAX_PER_DOCUMENT: Final = 3

_CITATION_ID_PREFIX: Final = "e"


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    """How to turn a retrieval hit inside one snapshot into a citable locator."""

    snapshot_id: str
    kind: CitationKind
    repo: str = ""
    commit_sha: str = ""
    captured_at: datetime | None = None

    def citation_locator(self, locator: str) -> str | None:
        """Return the durable citation locator, or ``None`` when it cannot be pinned."""
        if self.kind is CitationKind.WEB:
            return locator if locator.startswith(("http://", "https://")) else None
        if not self.repo or not self.commit_sha:
            return None
        return f"{self.repo}@{self.commit_sha}/{locator.lstrip('/')}"


@dataclass(frozen=True, slots=True)
class EvidenceBoosts:
    """Multipliers applied on top of the index score.

    These are editorial judgements, not calibrated weights: scores from different indexes
    are not on a common scale to begin with, and the point is only to order candidates the
    way a technical writer would.
    """

    exact_symbol: float = 2.5
    path_hint: float = 1.6
    title_term: float = 1.3


DEFAULT_BOOSTS: Final = EvidenceBoosts()


@dataclass(frozen=True, slots=True)
class EvidenceRequest:
    """What one chapter needs evidence for."""

    project_id: str
    chapter_slug: str
    chapter_title: str
    snapshot_ids: tuple[str, ...]
    questions: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    path_hints: tuple[str, ...] = ()
    max_items: int = DEFAULT_MAX_ITEMS
    char_budget: int = DEFAULT_CHAR_BUDGET
    per_query_limit: int = DEFAULT_PER_QUERY_LIMIT
    max_per_document: int = DEFAULT_MAX_PER_DOCUMENT

    def __post_init__(self) -> None:
        if not self.chapter_title.strip() and not self.questions and not self.symbols:
            raise ValueError("an evidence request needs a chapter title, a question or a symbol")
        if not self.snapshot_ids:
            raise ValueError(
                "an evidence request must name the approved snapshots; evidence outside them "
                "cannot be cited reproducibly"
            )


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """One citable piece of evidence, with why it ranked where it did."""

    snapshot_id: str
    kind: SearchKind
    citation_kind: CitationKind
    locator: str
    title: str
    excerpt: str
    score: float
    document_id: str | None = None
    chunk_id: str | None = None
    symbol_id: str | None = None
    retrieved_at: datetime | None = None
    reasons: tuple[str, ...] = field(default_factory=tuple)

    def to_citation(self, citation_id: str) -> EvidenceCitation:
        return EvidenceCitation(
            id=citation_id,
            kind=self.citation_kind,
            snapshot_id=self.snapshot_id,
            locator=self.locator,
            title=self.title,
            quote=self.excerpt,
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            symbol_id=self.symbol_id,
            retrieved_at=self.retrieved_at,
        )


@dataclass(frozen=True, slots=True)
class EvidencePack:
    """The evidence one chapter may cite, in the order it should be presented."""

    chapter_slug: str
    queries: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    items: tuple[EvidenceItem, ...] = ()
    truncated: bool = False

    @property
    def is_empty(self) -> bool:
        return not self.items

    def citation_ids(self) -> tuple[str, ...]:
        return tuple(f"{_CITATION_ID_PREFIX}{index}" for index in range(1, len(self.items) + 1))

    def by_citation_id(self) -> Mapping[str, EvidenceItem]:
        return dict(zip(self.citation_ids(), self.items, strict=True))

    def to_citations(self) -> tuple[EvidenceCitation, ...]:
        return tuple(
            item.to_citation(citation_id)
            for citation_id, item in self.by_citation_id().items()
        )

    def locators(self) -> frozenset[str]:
        return frozenset(item.locator for item in self.items)

    def by_locator(self) -> Mapping[str, EvidenceItem]:
        return {item.locator: item for item in self.items}


class EvidenceReranker(Protocol):
    """The extension point for a second retrieval stage, such as embeddings.

    Called with the merged, boosted candidates before the size limits are applied, so a
    reranker can change the order without having to know about budgets or citations.
    """

    def rerank(
        self,
        request: EvidenceRequest,
        items: Sequence[EvidenceItem],
    ) -> Sequence[EvidenceItem]: ...


class EvidencePackBuilder:
    """Turns retrieval results into a chapter's evidence pack."""

    def __init__(
        self,
        search: SearchService,
        *,
        snapshots: Mapping[str, SnapshotRef],
        boosts: EvidenceBoosts = DEFAULT_BOOSTS,
        reranker: EvidenceReranker | None = None,
    ) -> None:
        self._search = search
        self._snapshots = dict(snapshots)
        self._boosts = boosts
        self._reranker = reranker

    def build(self, request: EvidenceRequest) -> EvidencePack:
        queries = self._queries(request)
        merged: dict[str, EvidenceItem] = {}
        for query in queries:
            for hit in self._search.search(query):
                item = self._to_item(hit, request)
                if item is None:
                    continue
                existing = merged.get(item.locator)
                merged[item.locator] = item if existing is None else _better(existing, item)

        candidates = sorted(merged.values(), key=lambda item: (-item.score, item.locator))
        if self._reranker is not None:
            candidates = list(self._reranker.rerank(request, candidates))
        kept, truncated = _apply_limits(candidates, request)
        return EvidencePack(
            chapter_slug=request.chapter_slug,
            queries=tuple(query.text for query in queries),
            snapshot_ids=request.snapshot_ids,
            items=tuple(kept),
            truncated=truncated,
        )

    def _queries(self, request: EvidenceRequest) -> tuple[SearchQuery, ...]:
        texts = [request.chapter_title, *request.questions]
        queries = [
            SearchQuery(
                project_id=request.project_id,
                text=text,
                snapshot_ids=request.snapshot_ids,
                limit=request.per_query_limit,
            )
            for text in texts
            if text.strip()
        ]
        queries.extend(
            SearchQuery(
                project_id=request.project_id,
                text=symbol,
                snapshot_ids=request.snapshot_ids,
                kinds=(SearchKind.CODE_SYMBOL,),
                limit=request.per_query_limit,
            )
            for symbol in request.symbols
            if symbol.strip()
        )
        return tuple(queries)

    def _to_item(self, hit: SearchHit, request: EvidenceRequest) -> EvidenceItem | None:
        if hit.snapshot_id not in request.snapshot_ids:
            return None
        reference = self._snapshots.get(hit.snapshot_id)
        if reference is None:
            return None
        locator = reference.citation_locator(hit.locator)
        if locator is None:
            return None
        score, reasons = self._boosted(hit, request)
        return EvidenceItem(
            snapshot_id=hit.snapshot_id,
            kind=hit.kind,
            citation_kind=reference.kind,
            locator=locator,
            title=hit.title,
            excerpt=hit.excerpt,
            score=score,
            document_id=hit.document_id,
            chunk_id=hit.chunk_id,
            symbol_id=hit.symbol_id,
            retrieved_at=reference.captured_at,
            reasons=reasons,
        )

    def _boosted(self, hit: SearchHit, request: EvidenceRequest) -> tuple[float, tuple[str, ...]]:
        score = hit.score
        reasons: list[str] = []
        for symbol in request.symbols:
            if hit.symbol_id and _names_symbol(hit.title, symbol):
                score *= self._boosts.exact_symbol
                reasons.append(f"symbol:{symbol}")
        for hint in request.path_hints:
            if hint and hint.lower() in hit.locator.lower():
                score *= self._boosts.path_hint
                reasons.append(f"path:{hint}")
        for term in normalise_terms(request.chapter_title):
            if term.lower() in hit.title.lower():
                score *= self._boosts.title_term
                reasons.append(f"title:{term}")
        return score, tuple(reasons)


def snapshot_refs(session: Session, snapshot_ids: Iterable[str]) -> dict[str, SnapshotRef]:
    """Describe the approved snapshots so their hits can be turned into citations.

    Read-only, and deliberately here rather than in retrieval: a citation needs the source
    kind and the pinned commit, which are properties of the snapshot rather than of a hit.
    """
    wanted = tuple(dict.fromkeys(snapshot_ids))
    if not wanted:
        return {}
    rows = session.execute(
        sa.select(
            Snapshot.id,
            Snapshot.commit_sha,
            Snapshot.captured_at,
            Source.kind,
            Source.locator,
        )
        .join(Source, Snapshot.source_id == Source.id)
        .where(Snapshot.id.in_(wanted))
    ).all()
    return {
        row.id: SnapshotRef(
            snapshot_id=row.id,
            kind=(
                CitationKind.REPO if row.kind is SourceKind.GITHUB_REPO else CitationKind.WEB
            ),
            repo=row.locator if row.kind is SourceKind.GITHUB_REPO else "",
            commit_sha=row.commit_sha or "",
            captured_at=row.captured_at,
        )
        for row in rows
    }


def _names_symbol(title: str, symbol: str) -> bool:
    """Match a symbol query against a hit's qualified name, exactly on the final segment."""
    if not symbol:
        return False
    return title == symbol or title.split(".")[-1] == symbol


def _better(existing: EvidenceItem, candidate: EvidenceItem) -> EvidenceItem:
    """Keep the higher score for a location, merging the reasons both queries found."""
    reasons = tuple(dict.fromkeys((*existing.reasons, *candidate.reasons)))
    winner = existing if existing.score >= candidate.score else candidate
    return replace(winner, reasons=reasons)


def _apply_limits(
    candidates: Sequence[EvidenceItem],
    request: EvidenceRequest,
) -> tuple[list[EvidenceItem], bool]:
    kept: list[EvidenceItem] = []
    per_document: dict[str, int] = {}
    characters = 0
    truncated = False
    for item in candidates:
        key = item.document_id or item.locator
        if per_document.get(key, 0) >= request.max_per_document:
            truncated = True
            continue
        if len(kept) >= request.max_items:
            truncated = True
            break
        if characters + len(item.excerpt) > request.char_budget:
            truncated = True
            continue
        kept.append(item)
        per_document[key] = per_document.get(key, 0) + 1
        characters += len(item.excerpt)
    return kept, truncated
