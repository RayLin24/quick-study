"""The citation ledger: every claim, its sources, and whether it may be published.

The document model already refuses to hold an ungrounded claim, which stops the common case
at construction. The ledger answers the questions that need the evidence pack as well as the
document:

* was this citation actually retrieved for this chapter, or did the model produce a
  plausible-looking locator that nothing in the pack supports;
* does the quote attached to the citation appear in the retrieved excerpt;
* did an entailment check accept the claim against its source.

Failures are recorded with a reason rather than raised, because the audit trail is the point:
a run has to be able to show a reviewer which claims were dropped and why. A teaching
abstraction is allowed to stand without evidence; every claim that describes the subject is
not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from app.db.models.enums import ClaimKind, ClaimStatus
from app.tutorial.evidence import EvidencePack
from app.tutorial.schema import (
    GROUNDED_CLAIM_KINDS,
    CodeBlock,
    EvidenceCitation,
    TutorialDocument,
)

#: How much of a code sample identifies it in the audit trail.
_CODE_STATEMENT_LIMIT: Final = 200


class EntailmentVerdict(StrEnum):
    """Whether the cited source actually supports the claim."""

    #: No check has run yet. The default: an unchecked claim is not a rejected one.
    NOT_CHECKED = "not_checked"
    SUPPORTED = "supported"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


class RejectionReason(StrEnum):
    """Why a claim may not be published."""

    MISSING_CITATION = "missing_citation"
    #: The citation does not correspond to anything retrieved for this chapter.
    UNKNOWN_CITATION = "unknown_citation"
    OFF_SNAPSHOT_CITATION = "off_snapshot_citation"
    QUOTE_NOT_IN_SOURCE = "quote_not_in_source"
    UNSUPPORTED_BY_SOURCE = "unsupported_by_source"


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    """One claim as the ledger sees it."""

    claim_id: str
    chapter_slug: str
    statement: str
    kind: ClaimKind
    citation_ids: tuple[str, ...]
    locators: tuple[str, ...]
    verdict: EntailmentVerdict
    status: ClaimStatus
    reason: RejectionReason | None = None
    note: str = ""

    @property
    def rejected(self) -> bool:
        return self.status is ClaimStatus.REJECTED

    def as_record(self) -> dict[str, Any]:
        """Return a plain mapping for the workflow to persist as a claim plus citations."""
        return {
            "claim_id": self.claim_id,
            "chapter_slug": self.chapter_slug,
            "statement": self.statement,
            "kind": self.kind.value,
            "status": self.status.value,
            "verdict": self.verdict.value,
            "citation_ids": list(self.citation_ids),
            "locators": list(self.locators),
            "reason": self.reason.value if self.reason else None,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class CitationAudit:
    """The outcome of auditing a whole document."""

    entries: tuple[LedgerEntry, ...]

    @property
    def rejected(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self.entries if entry.rejected)

    @property
    def verified(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self.entries if entry.status is ClaimStatus.VERIFIED)

    @property
    def is_clean(self) -> bool:
        return not self.rejected

    def as_records(self) -> tuple[dict[str, Any], ...]:
        return tuple(entry.as_record() for entry in self.entries)


class CitationLedger:
    """Records claims and decides, per claim, whether the evidence holds them up."""

    def __init__(self, packs: Mapping[str, EvidencePack] | None = None) -> None:
        self._packs = dict(packs or {})
        self._entries: list[LedgerEntry] = []

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries)

    @property
    def rejected(self) -> tuple[LedgerEntry, ...]:
        return tuple(entry for entry in self._entries if entry.rejected)

    def record(
        self,
        *,
        claim_id: str,
        chapter_slug: str,
        statement: str,
        kind: ClaimKind,
        citations: Sequence[EvidenceCitation],
        verdict: EntailmentVerdict = EntailmentVerdict.NOT_CHECKED,
        approved_snapshot_ids: Iterable[str] = (),
        missing_citation_ids: Iterable[str] = (),
    ) -> LedgerEntry:
        """Judge one claim and keep the result.

        ``missing_citation_ids`` are ids the claim referenced that the document could not
        resolve; they are reported rather than silently ignored.
        """
        if any(
            entry.claim_id == claim_id and entry.chapter_slug == chapter_slug
            for entry in self._entries
        ):
            raise ValueError(f"claim {claim_id!r} of {chapter_slug!r} is already in the ledger")
        entry = self._judge(
            claim_id=claim_id,
            chapter_slug=chapter_slug,
            statement=statement,
            kind=kind,
            citations=tuple(citations),
            verdict=verdict,
            approved_snapshot_ids=frozenset(approved_snapshot_ids),
            missing_citation_ids=tuple(missing_citation_ids),
        )
        self._entries.append(entry)
        return entry

    def audit_document(
        self,
        document: TutorialDocument,
        *,
        verdicts: Mapping[str, EntailmentVerdict] | None = None,
    ) -> CitationAudit:
        """Audit every claim a document makes, including its non-illustrative code samples.

        ``verdicts`` come from whatever entailment check ran, keyed by claim id or by
        ``chapter-slug:claim-id`` when the same id appears in more than one chapter.
        """
        checked = _Verdicts(verdicts or {})
        approved = frozenset(document.metadata.snapshot_ids)
        index = document.citation_index()
        entries: list[LedgerEntry] = []

        for chapter in document.chapters:
            for fact in chapter.facts:
                resolved, missing = _resolve(fact.citation_ids, index)
                entries.append(
                    self.record(
                        claim_id=fact.id,
                        chapter_slug=chapter.slug,
                        statement=fact.statement,
                        kind=fact.kind,
                        citations=resolved,
                        verdict=checked.of(chapter.slug, fact.id),
                        approved_snapshot_ids=approved,
                        missing_citation_ids=missing,
                    )
                )
            for position, block in enumerate(chapter.blocks):
                if not isinstance(block, CodeBlock) or block.illustrative:
                    continue
                claim_id = f"{chapter.slug}:code:{position}"
                resolved, missing = _resolve(block.citation_ids, index)
                entries.append(
                    self.record(
                        claim_id=claim_id,
                        chapter_slug=chapter.slug,
                        statement=_code_statement(block),
                        kind=ClaimKind.API_SIGNATURE,
                        citations=resolved,
                        verdict=checked.of(chapter.slug, claim_id),
                        approved_snapshot_ids=approved,
                        missing_citation_ids=missing,
                    )
                )
        return CitationAudit(entries=tuple(entries))

    def _judge(
        self,
        *,
        claim_id: str,
        chapter_slug: str,
        statement: str,
        kind: ClaimKind,
        citations: tuple[EvidenceCitation, ...],
        verdict: EntailmentVerdict,
        approved_snapshot_ids: frozenset[str],
        missing_citation_ids: tuple[str, ...],
    ) -> LedgerEntry:
        def entry(
            status: ClaimStatus,
            reason: RejectionReason | None = None,
            note: str = "",
        ) -> LedgerEntry:
            return LedgerEntry(
                claim_id=claim_id,
                chapter_slug=chapter_slug,
                statement=statement,
                kind=kind,
                citation_ids=tuple(citation.id for citation in citations)
                + missing_citation_ids,
                locators=tuple(citation.locator for citation in citations),
                verdict=verdict,
                status=status,
                reason=reason,
                note=note,
            )

        if missing_citation_ids:
            return entry(
                ClaimStatus.REJECTED,
                RejectionReason.UNKNOWN_CITATION,
                f"cited ids that do not exist: {', '.join(missing_citation_ids)}",
            )

        if not citations:
            if kind in GROUNDED_CLAIM_KINDS:
                return entry(
                    ClaimStatus.REJECTED,
                    RejectionReason.MISSING_CITATION,
                    f"a {kind.value} claim cannot be published without a source",
                )
            return entry(ClaimStatus.UNVERIFIED)

        pack = self._packs.get(chapter_slug)
        for citation in citations:
            if approved_snapshot_ids and citation.snapshot_id not in approved_snapshot_ids:
                return entry(
                    ClaimStatus.REJECTED,
                    RejectionReason.OFF_SNAPSHOT_CITATION,
                    f"{citation.locator} is outside the approved snapshots",
                )
            if pack is None:
                continue
            item = pack.by_locator().get(citation.locator)
            if item is None:
                return entry(
                    ClaimStatus.REJECTED,
                    RejectionReason.UNKNOWN_CITATION,
                    f"{citation.locator} was not retrieved for this chapter",
                )
            if citation.quote and not _quotes(item.excerpt, citation.quote):
                return entry(
                    ClaimStatus.REJECTED,
                    RejectionReason.QUOTE_NOT_IN_SOURCE,
                    f"the quote attached to {citation.id} is not in {citation.locator}",
                )

        if verdict is EntailmentVerdict.UNSUPPORTED:
            return entry(
                ClaimStatus.REJECTED,
                RejectionReason.UNSUPPORTED_BY_SOURCE,
                "the entailment check found the source does not support this claim",
            )
        if verdict is EntailmentVerdict.SUPPORTED:
            return entry(ClaimStatus.VERIFIED)
        return entry(ClaimStatus.UNVERIFIED)


class _Verdicts:
    """Looks a verdict up by qualified key first, then by bare claim id."""

    def __init__(self, verdicts: Mapping[str, EntailmentVerdict]) -> None:
        self._verdicts = verdicts

    def of(self, chapter_slug: str, claim_id: str) -> EntailmentVerdict:
        qualified = self._verdicts.get(f"{chapter_slug}:{claim_id}")
        return qualified or self._verdicts.get(claim_id, EntailmentVerdict.NOT_CHECKED)


def _resolve(
    citation_ids: Sequence[str],
    index: Mapping[str, EvidenceCitation],
) -> tuple[tuple[EvidenceCitation, ...], tuple[str, ...]]:
    resolved = tuple(index[citation_id] for citation_id in citation_ids if citation_id in index)
    missing = tuple(citation_id for citation_id in citation_ids if citation_id not in index)
    return resolved, missing


def _quotes(source: str, quote: str) -> bool:
    """Compare a quote to its source ignoring how either was wrapped.

    Retrieval collapses whitespace when it builds an excerpt and a model reflows text it
    copies, so comparing raw strings would reject correct quotes.
    """
    return _collapse(quote) in _collapse(source)


def _collapse(text: str) -> str:
    return " ".join(text.split()).casefold()


def _code_statement(block: CodeBlock) -> str:
    caption = block.caption or block.code.strip().splitlines()[0]
    return f"code sample ({block.language}): {caption[:_CODE_STATEMENT_LIMIT]}"
