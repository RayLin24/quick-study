"""The citation ledger.

The document model already refuses to hold an ungrounded claim. The ledger answers the
questions that need the evidence pack as well as the document: does this citation point at
something that was actually retrieved for this chapter, does the quote appear in the source,
and did an entailment check accept the claim? Anything that fails is recorded as rejected
with a reason, because a run has to be able to say why a claim did not make it into the
published tutorial.
"""

from __future__ import annotations

import pytest
from test_tutorial_evidence import SCOPE, request_for
from tutorial_support import (
    FakeSearchService,
    chapter,
    citation,
    code_block,
    document,
    fact,
    markdown_block,
    metadata,
    repo_citation,
    search_hit,
)

from app.db.models.enums import ClaimKind, ClaimStatus
from app.tutorial.citations import (
    CitationLedger,
    EntailmentVerdict,
    RejectionReason,
)
from app.tutorial.evidence import EvidencePackBuilder
from app.tutorial.schema import TutorialDocument


def pack_for(*hits: object) -> object:
    search = FakeSearchService(default=list(hits or (search_hit(),)))  # type: ignore[arg-type]
    return EvidencePackBuilder(search, snapshots=SCOPE).build(request_for())


def document_from_pack() -> TutorialDocument:
    """A document whose single citation is exactly what the pack retrieved."""
    pack = pack_for()
    citations = pack.to_citations()  # type: ignore[attr-defined]
    return document(
        metadata=metadata(snapshot_ids=tuple({item.snapshot_id for item in citations})),
        citations=citations,
        chapters=(
            chapter(
                blocks=(markdown_block(citation_ids=("e1",)),),
                facts=(fact(citation_ids=("e1",)),),
                exercises=(),
            ),
        ),
        glossary=(),
    )


class TestGroundedClaims:
    def test_a_claim_backed_by_retrieved_evidence_is_accepted(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})

        audit = ledger.audit_document(document_from_pack())

        assert audit.is_clean
        assert [entry.status for entry in audit.entries] == [ClaimStatus.UNVERIFIED]

    def test_an_entailed_claim_is_marked_verified(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        built = document_from_pack()
        claim_id = built.chapters[0].facts[0].id

        audit = ledger.audit_document(
            built, verdicts={claim_id: EntailmentVerdict.SUPPORTED}
        )

        assert audit.entries[0].status is ClaimStatus.VERIFIED
        assert audit.entries[0].verdict is EntailmentVerdict.SUPPORTED

    def test_a_claim_the_source_does_not_support_is_rejected(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        built = document_from_pack()
        claim_id = built.chapters[0].facts[0].id

        audit = ledger.audit_document(
            built, verdicts={claim_id: EntailmentVerdict.UNSUPPORTED}
        )

        assert not audit.is_clean
        assert audit.rejected[0].reason is RejectionReason.UNSUPPORTED_BY_SOURCE


class TestUngroundedClaims:
    def test_a_fact_with_no_citation_is_rejected(self) -> None:
        """The document model blocks this too; the ledger says why for the audit trail."""
        ledger = CitationLedger()

        entry = ledger.record(
            claim_id="f9",
            chapter_slug="deployment",
            statement="The gateway restarts every hour.",
            kind=ClaimKind.FACT,
            citations=(),
        )

        assert entry.status is ClaimStatus.REJECTED
        assert entry.reason is RejectionReason.MISSING_CITATION

    def test_a_teaching_abstraction_may_stand_without_evidence(self) -> None:
        ledger = CitationLedger()

        entry = ledger.record(
            claim_id="f10",
            chapter_slug="deployment",
            statement="Think of the supervisor as a night watchman.",
            kind=ClaimKind.TEACHING_ABSTRACTION,
            citations=(),
        )

        assert entry.status is not ClaimStatus.REJECTED
        assert entry.verdict is EntailmentVerdict.NOT_CHECKED

    def test_a_citation_that_was_never_retrieved_is_treated_as_invented(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        fabricated = citation(id="e1", locator="https://docs.example.test/never-fetched")
        built = document(
            citations=(fabricated, repo_citation()),
            chapters=(chapter(exercises=(), facts=(fact(citation_ids=("e1",)),)),),
            glossary=(),
        )

        audit = ledger.audit_document(built)

        assert audit.rejected
        assert audit.rejected[0].reason is RejectionReason.UNKNOWN_CITATION

    def test_a_quote_that_is_not_in_the_source_is_rejected(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        built = document_from_pack()
        misquoted = built.model_copy(
            update={
                "citations": (
                    built.citations[0].model_copy(
                        update={"quote": "The gateway needs no supervisor at all."}
                    ),
                )
            }
        )

        audit = ledger.audit_document(misquoted)

        assert audit.rejected[0].reason is RejectionReason.QUOTE_NOT_IN_SOURCE

    def test_a_quote_is_compared_ignoring_whitespace_differences(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        built = document_from_pack()
        reflowed = built.model_copy(
            update={
                "citations": (
                    built.citations[0].model_copy(
                        update={"quote": "Deploy the gateway service\n  behind the supervisor"}
                    ),
                )
            }
        )

        audit = ledger.audit_document(reflowed)

        assert audit.is_clean


class TestCodeSamples:
    def test_a_code_block_is_audited_as_a_claim_about_its_source(self) -> None:
        pack = pack_for()
        built = document(
            metadata=metadata(snapshot_ids=(pack.items[0].snapshot_id,)),  # type: ignore[attr-defined]
            citations=pack.to_citations(),  # type: ignore[attr-defined]
            chapters=(
                chapter(
                    blocks=(code_block(citation_ids=("e1",)),),
                    facts=(),
                    exercises=(),
                ),
            ),
            glossary=(),
        )

        audit = CitationLedger({"deployment": pack}).audit_document(built)

        assert [entry.kind for entry in audit.entries] == [ClaimKind.API_SIGNATURE]
        assert audit.is_clean

    def test_illustrative_code_is_not_audited_as_a_claim(self) -> None:
        pack = pack_for()
        built = document(
            metadata=metadata(snapshot_ids=(pack.items[0].snapshot_id,)),  # type: ignore[attr-defined]
            citations=pack.to_citations(),  # type: ignore[attr-defined]
            chapters=(
                chapter(
                    blocks=(code_block(citation_ids=(), illustrative=True),),
                    facts=(),
                    exercises=(),
                ),
            ),
            glossary=(),
        )

        audit = CitationLedger({"deployment": pack}).audit_document(built)

        assert audit.entries == ()


class TestAuditRecords:
    def test_the_audit_can_be_handed_to_the_workflow_for_persistence(self) -> None:
        ledger = CitationLedger({"deployment": pack_for()})
        built = document_from_pack()

        records = ledger.audit_document(built).as_records()

        assert records[0]["statement"] == built.chapters[0].facts[0].statement
        assert records[0]["kind"] == ClaimKind.FACT.value
        assert records[0]["status"] == ClaimStatus.UNVERIFIED.value
        assert records[0]["locators"] == [built.citations[0].locator]
        assert records[0]["chapter_slug"] == "deployment"

    def test_the_ledger_keeps_every_entry_it_recorded(self) -> None:
        ledger = CitationLedger()
        ledger.record(
            claim_id="f1",
            chapter_slug="deployment",
            statement="A",
            kind=ClaimKind.TEACHING_ABSTRACTION,
            citations=(),
        )
        ledger.record(
            claim_id="f2",
            chapter_slug="deployment",
            statement="B",
            kind=ClaimKind.FACT,
            citations=(),
        )

        assert len(ledger.entries) == 2
        assert len(ledger.rejected) == 1

    def test_a_claim_id_cannot_be_recorded_twice(self) -> None:
        ledger = CitationLedger()
        ledger.record(
            claim_id="f1",
            chapter_slug="deployment",
            statement="A",
            kind=ClaimKind.TEACHING_ABSTRACTION,
            citations=(),
        )

        with pytest.raises(ValueError):
            ledger.record(
                claim_id="f1",
                chapter_slug="deployment",
                statement="A again",
                kind=ClaimKind.TEACHING_ABSTRACTION,
                citations=(),
            )
