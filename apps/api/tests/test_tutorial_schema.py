"""The canonical tutorial document.

There is one content model. The Markdown bundle and the published web page are both
rendered from it, because two independent representations of the same tutorial drift within
a release, and a reader cannot tell which one the citations belong to.

The model is also where the citation rules are structural rather than advisory: a fact that
states something about the subject cannot exist without a citation, a citation cannot point
outside the approved snapshots, and a code block nobody can trace to a source has to be
labelled as illustrative.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tutorial_support import (
    SNAPSHOT_ID,
    chapter,
    citation,
    code_block,
    diagram,
    diagram_block,
    document,
    exercise,
    fact,
    glossary_term,
    markdown_block,
    metadata,
    repo_citation,
)

from app.db.models.enums import CitationKind, ClaimKind, ClaimStatus
from app.tutorial.mermaid_ir import DiagramNode, NodeRole
from app.tutorial.schema import SCHEMA_VERSION, TutorialDocument


class TestDocumentShape:
    def test_a_complete_document_validates(self) -> None:
        built = document()

        assert built.metadata.schema_version == SCHEMA_VERSION
        assert [chapter.slug for chapter in built.chapters] == ["deployment"]
        assert built.citation_index()["e1"].locator.startswith("https://")

    def test_every_block_kind_can_appear_in_a_chapter(self) -> None:
        built = document(
            chapters=(chapter(blocks=(markdown_block(), code_block(), diagram_block())),)
        )

        assert [block.type for block in built.chapters[0].blocks] == [
            "markdown",
            "code",
            "diagram",
        ]

    def test_blocks_are_iterable_across_the_whole_document(self) -> None:
        built = document(
            chapters=(
                chapter(),
                chapter(slug="operations", ordinal=1, title="Operating it"),
            )
        )

        assert len(list(built.iter_blocks())) == 4

    def test_a_chapter_needs_at_least_one_block(self) -> None:
        with pytest.raises(ValidationError):
            chapter(blocks=())

    def test_chapter_slugs_are_unique(self) -> None:
        with pytest.raises(ValidationError) as failure:
            document(chapters=(chapter(), chapter(ordinal=1)))

        assert "duplicate" in str(failure.value).lower()

    def test_chapter_ordinals_are_the_reading_order_without_gaps(self) -> None:
        with pytest.raises(ValidationError) as failure:
            document(chapters=(chapter(), chapter(slug="operations", ordinal=5)))

        assert "ordinal" in str(failure.value).lower()

    def test_a_document_needs_a_chapter(self) -> None:
        with pytest.raises(ValidationError):
            document(chapters=())

    def test_an_unknown_field_is_refused_so_a_model_cannot_smuggle_content_in(self) -> None:
        payload = document().model_dump(mode="json")
        payload["appendix"] = "extra"

        with pytest.raises(ValidationError):
            TutorialDocument.model_validate(payload)

    def test_the_document_is_immutable_once_validated(self) -> None:
        built = document()

        with pytest.raises(ValidationError):
            built.metadata = metadata(title="Something else")  # type: ignore[misc]


class TestCitationIntegrity:
    def test_citation_identifiers_are_unique(self) -> None:
        with pytest.raises(ValidationError) as failure:
            document(citations=(citation(), citation()))

        assert "duplicate" in str(failure.value).lower()

    def test_a_block_may_not_reference_a_citation_that_does_not_exist(self) -> None:
        with pytest.raises(ValidationError) as failure:
            document(chapters=(chapter(blocks=(markdown_block(citation_ids=("e9",)),)),))

        assert "e9" in str(failure.value)

    def test_a_fact_may_not_reference_a_citation_that_does_not_exist(self) -> None:
        with pytest.raises(ValidationError) as failure:
            document(chapters=(chapter(facts=(fact(citation_ids=("nope",)),)),))

        assert "nope" in str(failure.value)

    def test_an_exercise_reference_is_resolved_too(self) -> None:
        with pytest.raises(ValidationError):
            document(chapters=(chapter(exercises=(exercise(citation_ids=("e9",)),)),))

    def test_a_glossary_reference_is_resolved_too(self) -> None:
        with pytest.raises(ValidationError):
            document(glossary=(glossary_term(citation_ids=("e9",)),))

    def test_a_diagram_node_reference_is_resolved_too(self) -> None:
        unresolved = diagram(
            nodes=(
                DiagramNode(
                    id="Gateway",
                    label="Gateway",
                    role=NodeRole.SERVICE,
                    citation_ids=("e7",),
                ),
                DiagramNode(
                    id="Supervisor",
                    label="Supervisor",
                    role=NodeRole.SERVICE,
                    citation_ids=("e1",),
                ),
            )
        )

        with pytest.raises(ValidationError) as failure:
            document(chapters=(chapter(blocks=(diagram_block(diagram=unresolved),)),))

        assert "e7" in str(failure.value)

    def test_evidence_must_come_from_an_approved_snapshot(self) -> None:
        """A citation outside the approved scope is either stale or invented."""
        with pytest.raises(ValidationError) as failure:
            document(citations=(citation(snapshot_id="snapshot-unapproved"), repo_citation()))

        assert "snapshot" in str(failure.value).lower()

    def test_a_web_citation_points_at_a_fetched_url(self) -> None:
        with pytest.raises(ValidationError):
            citation(locator="not-a-url")

    def test_a_repository_citation_is_pinned_to_a_commit_and_lines(self) -> None:
        with pytest.raises(ValidationError) as failure:
            repo_citation(locator="octocat/hello-world/src/gateway.py")

        assert "commit" in str(failure.value).lower()

    def test_a_repository_citation_with_a_commit_and_line_range_is_accepted(self) -> None:
        assert repo_citation().kind is CitationKind.REPO

    def test_a_citation_needs_a_locator(self) -> None:
        with pytest.raises(ValidationError):
            citation(locator="")


class TestFactsMustBeGrounded:
    def test_a_statement_about_the_subject_cannot_exist_without_a_citation(self) -> None:
        with pytest.raises(ValidationError) as failure:
            fact(citation_ids=())

        assert "citation" in str(failure.value).lower()

    @pytest.mark.parametrize(
        "kind", [ClaimKind.FACT, ClaimKind.API_SIGNATURE, ClaimKind.BEHAVIOUR]
    )
    def test_every_externally_checkable_claim_kind_needs_evidence(self, kind: ClaimKind) -> None:
        with pytest.raises(ValidationError):
            fact(kind=kind, citation_ids=())

    def test_a_teaching_abstraction_may_stand_on_its_own(self) -> None:
        """An analogy invented for the reader has no source to cite, and says so."""
        standalone = fact(kind=ClaimKind.TEACHING_ABSTRACTION, citation_ids=())

        assert standalone.status is ClaimStatus.UNVERIFIED

    def test_a_fact_needs_a_statement(self) -> None:
        with pytest.raises(ValidationError):
            fact(statement="   ")

    def test_confidence_stays_within_zero_and_one(self) -> None:
        with pytest.raises(ValidationError):
            fact(confidence=1.5)


class TestCodeBlocks:
    def test_uncited_code_has_to_be_labelled_illustrative(self) -> None:
        with pytest.raises(ValidationError) as failure:
            code_block(citation_ids=())

        assert "illustrative" in str(failure.value).lower()

    def test_illustrative_code_is_allowed_without_a_source(self) -> None:
        assert code_block(citation_ids=(), illustrative=True).illustrative

    def test_code_needs_a_language_so_it_can_be_syntax_checked(self) -> None:
        with pytest.raises(ValidationError):
            code_block(language="")

    def test_code_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            code_block(code="\n  \n")

    def test_verification_defaults_to_unverified(self) -> None:
        assert code_block().verified is False


class TestMarkdownBlocks:
    def test_markdown_cannot_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            markdown_block(markdown="   ")

    def test_markdown_may_carry_no_citation_when_it_only_navigates(self) -> None:
        """Section scaffolding is not a claim; the facts around it carry the evidence."""
        assert markdown_block(citation_ids=()).citation_ids == ()


class TestSerialisationStability:
    def test_a_document_round_trips_through_json_unchanged(self) -> None:
        original = document()

        assert TutorialDocument.model_validate_json(original.model_dump_json()) == original

    def test_the_canonical_form_does_not_depend_on_input_key_order(self) -> None:
        payload = document().model_dump(mode="json")
        shuffled = json.loads(json.dumps(payload, sort_keys=True))

        assert TutorialDocument.model_validate(shuffled).canonical_json() == (
            TutorialDocument.model_validate(payload).canonical_json()
        )

    def test_the_digest_identifies_the_content(self) -> None:
        first = document()
        second = document()

        assert first.content_digest() == second.content_digest()
        assert len(first.content_digest()) == 64

    def test_the_digest_changes_when_the_content_does(self) -> None:
        changed = document(chapters=(chapter(title="Deploying the gateway service"),))

        assert changed.content_digest() != document().content_digest()

    def test_the_canonical_form_is_deterministic_bytes(self) -> None:
        assert document().canonical_json() == document().canonical_json()

    def test_the_snapshot_scope_is_part_of_the_document_metadata(self) -> None:
        assert document().metadata.snapshot_ids == (SNAPSHOT_ID,)
