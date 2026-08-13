"""The generation sequence the workflow graph calls.

Corpus map, module summaries, evidence outline, approval, per-chapter evidence, chapter
generation, then a global consistency pass. Every step here is a plain function: the graph
owns orchestration, interrupts and persistence, and this module owns what each step means.

Two rules shape the design. Summaries are navigation only -- they help decide what to write
about and never become the source of a fact, which must cite a snapshot. And the consistency
pass returns a constrained set of edits rather than rewritten prose, because a model handed a
finished document back to rewrite will quietly drop citations.
"""

from __future__ import annotations

import pytest
from conftest import make_document, make_project, make_snapshot, make_symbol
from llm_support import STRUCTURED_SPEC, FakeChatModel, reply
from sqlalchemy.orm import Session
from test_tutorial_evidence import SCOPE, request_for
from tutorial_support import FakeSearchService, search_hit

from app.db.models.enums import ApprovalDecision, ClaimKind, CodeLanguage, DocumentKind
from app.llm.errors import StructuredOutputInvalid
from app.llm.usage import UsageLedger
from app.tutorial.citations import RejectionReason
from app.tutorial.evidence import EvidencePackBuilder
from app.tutorial.pipeline import (
    GENERATION_STEPS,
    ChapterDraft,
    ConsistencyRevision,
    EvidenceMissing,
    ModuleSummaries,
    ModuleSummary,
    OutlineChapterProposal,
    OutlineProposal,
    apply_outline_decision,
    assemble_document,
    build_chapter_evidence,
    build_corpus_map,
    draft_evidence_outline,
    generate_chapter,
    outline_approval_payload,
    revise_for_consistency,
    summarise_modules,
)
from app.tutorial.schema import AtomicFact, CodeBlock, MarkdownBlock, TutorialMetadata

PLAN = OutlineChapterProposal(
    slug="deployment",
    title="Deploying the gateway",
    intent="Explain how the gateway is deployed and supervised.",
    questions=("How is the gateway supervised?",),
    path_hints=("deploy",),
    symbols=("build_gateway",),
)


def pack(*hits: object):
    search = FakeSearchService(default=list(hits or (search_hit(),)))
    return EvidencePackBuilder(search, snapshots=SCOPE).build(request_for())


def draft(**overrides: object) -> ChapterDraft:
    payload: dict[str, object] = {
        "title": "Deploying the gateway",
        "summary": "How the gateway is deployed and supervised.",
        "blocks": (
            MarkdownBlock(
                markdown="The gateway runs behind a supervisor process.",
                citation_ids=("e1",),
            ),
        ),
        "facts": (
            AtomicFact(
                id="f1",
                statement="The gateway runs behind a supervisor process.",
                kind=ClaimKind.FACT,
                citation_ids=("e1",),
            ),
        ),
    }
    payload.update(overrides)
    return ChapterDraft(**payload)  # type: ignore[arg-type]


def metadata_for(**overrides: object) -> TutorialMetadata:
    payload: dict[str, object] = {
        "project_id": "project-1",
        "run_id": "run-1",
        "title": "Gateway tutorial",
        "slug": "gateway-tutorial",
        "snapshot_ids": tuple(SCOPE),
    }
    payload.update(overrides)
    return TutorialMetadata(**payload)  # type: ignore[arg-type]


def test_the_generation_sequence_is_recorded_in_order() -> None:
    assert GENERATION_STEPS == (
        "corpus_map",
        "module_summaries",
        "evidence_outline",
        "outline_approval",
        "chapter_evidence",
        "chapter_generation",
        "consistency_revision",
    )


class TestCorpusMap:
    def seed(self, db: Session) -> tuple[str, str]:
        project = make_project(db, slug="mapped")
        snapshot = make_snapshot(db, project=project, locator="octocat/hello-world")
        for path in ("src/gateway/factory.py", "src/gateway/config.py", "docs/deploy.md"):
            document = make_document(
                db,
                snapshot=snapshot,
                kind=DocumentKind.REPO_FILE,
                path=path,
                uri=f"https://github.test/octocat/hello-world/blob/main/{path}",
                title=path.rsplit("/", 1)[-1],
                code_language=CodeLanguage.PYTHON,
            )
            if path.endswith("factory.py"):
                make_symbol(db, document=document)
        db.commit()
        return project.id, snapshot.id

    def test_repository_files_are_grouped_by_directory(self, db: Session) -> None:
        project_id, snapshot_id = self.seed(db)

        corpus = build_corpus_map(db, project_id=project_id, snapshot_ids=(snapshot_id,))

        keys = [group.key for group in corpus.groups]
        assert "src/gateway" in keys
        assert corpus.document_count == 3
        assert corpus.groups[0].document_count == 2

    def test_a_group_carries_examples_and_its_top_symbols(self, db: Session) -> None:
        project_id, snapshot_id = self.seed(db)

        corpus = build_corpus_map(db, project_id=project_id, snapshot_ids=(snapshot_id,))
        gateway = next(group for group in corpus.groups if group.key == "src/gateway")

        assert gateway.sample_locators
        assert "gateway.factory.build_gateway" in gateway.top_symbols
        assert CodeLanguage.PYTHON in gateway.languages

    def test_web_pages_are_grouped_by_their_first_path_segment(self, db: Session) -> None:
        project = make_project(db, slug="site")
        snapshot = make_snapshot(db, project=project)
        for path in ("/guides/deploy", "/guides/configure", "/reference/api"):
            make_document(
                db,
                snapshot=snapshot,
                path=path,
                uri=f"https://docs.example.test{path}",
                title=path,
            )
        db.commit()

        corpus = build_corpus_map(db, project_id=project.id, snapshot_ids=(snapshot.id,))

        assert [group.key for group in corpus.groups][0] == "guides"

    def test_the_map_is_confined_to_the_project_and_snapshots(self, db: Session) -> None:
        project_id, snapshot_id = self.seed(db)
        other = make_project(db, slug="other")
        other_snapshot = make_snapshot(db, project=other)
        make_document(db, snapshot=other_snapshot, path="/elsewhere")
        db.commit()

        corpus = build_corpus_map(db, project_id=project_id, snapshot_ids=(snapshot_id,))

        assert corpus.document_count == 3

    def test_the_group_count_is_capped_and_reported(self, db: Session) -> None:
        project_id, snapshot_id = self.seed(db)

        corpus = build_corpus_map(
            db, project_id=project_id, snapshot_ids=(snapshot_id,), max_groups=1
        )

        assert len(corpus.groups) == 1
        assert corpus.truncated

    def test_the_overview_is_deterministic_text_for_the_outline_prompt(
        self,
        db: Session,
    ) -> None:
        project_id, snapshot_id = self.seed(db)

        corpus = build_corpus_map(db, project_id=project_id, snapshot_ids=(snapshot_id,))

        assert corpus.overview() == corpus.overview()
        assert "src/gateway" in corpus.overview()

    def test_an_empty_corpus_maps_to_nothing_rather_than_failing(self, db: Session) -> None:
        project = make_project(db, slug="empty")

        corpus = build_corpus_map(db, project_id=project.id, snapshot_ids=("none",))

        assert corpus.groups == ()
        assert corpus.document_count == 0


class TestModuleSummaries:
    def test_summaries_come_back_marked_as_navigation_only(self, db: Session) -> None:
        summaries = ModuleSummaries(
            summaries=(
                ModuleSummary(
                    group_key="src/gateway",
                    title="Gateway package",
                    purpose="Builds and supervises the gateway service.",
                ),
            )
        )
        model = FakeChatModel([reply(summaries.model_dump_json())])
        project = make_project(db, slug="summarised")
        db.commit()
        corpus = build_corpus_map(db, project_id=project.id, snapshot_ids=("none",))

        result = summarise_modules(model, corpus)

        assert result.value.summaries[0].navigation_only is True
        assert result.value.notes() == (
            "Gateway package: Builds and supervises the gateway service.",
        )

    def test_the_corpus_overview_is_what_the_model_is_shown(self, db: Session) -> None:
        summaries = ModuleSummaries(
            summaries=(ModuleSummary(group_key="k", title="T", purpose="P"),)
        )
        model = FakeChatModel([reply(summaries.model_dump_json())])
        project = make_project(db, slug="shown")
        make_document(db, snapshot=make_snapshot(db, project=project), path="/guides/deploy")
        db.commit()
        corpus = build_corpus_map(db, project_id=project.id, snapshot_ids=())

        summarise_modules(model, corpus)

        assert corpus.overview() in model.requests[0].messages[-2].content

    def test_the_call_is_charged_to_the_run_ledger(self, db: Session) -> None:
        summaries = ModuleSummaries(
            summaries=(ModuleSummary(group_key="k", title="T", purpose="P"),)
        )
        model = FakeChatModel([reply(summaries.model_dump_json())])
        project = make_project(db, slug="charged")
        db.commit()
        corpus = build_corpus_map(db, project_id=project.id, snapshot_ids=())
        ledger = UsageLedger()

        summarise_modules(model, corpus, ledger=ledger)

        assert ledger.step_fields()["tokens_in"] == 100


class TestEvidenceOutline:
    def proposal(self, **overrides: object) -> OutlineProposal:
        payload: dict[str, object] = {
            "title": "Gateway tutorial",
            "summary": "A tour of the gateway.",
            "chapters": (PLAN,),
        }
        payload.update(overrides)
        return OutlineProposal(**payload)  # type: ignore[arg-type]

    def test_a_proposal_names_the_evidence_each_chapter_needs(self, db: Session) -> None:
        model = FakeChatModel([reply(self.proposal().model_dump_json())])
        corpus = build_corpus_map(
            db, project_id=make_project(db, slug="outlined").id, snapshot_ids=()
        )

        result = draft_evidence_outline(
            model,
            corpus,
            tutorial_title="Gateway tutorial",
            reader_level="beginner",
            length_preset="standard",
        )

        chapter = result.value.chapters[0]
        assert chapter.questions
        assert chapter.symbols == ("build_gateway",)

    def test_a_chapter_without_a_question_is_refused(self) -> None:
        with pytest.raises(ValueError):
            OutlineChapterProposal(
                slug="deployment", title="Deploying", intent="Explain", questions=()
            )

    def test_duplicate_chapter_slugs_are_refused(self) -> None:
        with pytest.raises(ValueError):
            OutlineProposal(title="t", summary="s", chapters=(PLAN, PLAN))

    def test_an_invalid_proposal_is_repaired_before_it_reaches_the_reviewer(
        self,
        db: Session,
    ) -> None:
        model = FakeChatModel(
            [reply('{"title": "t", "summary": "s", "chapters": []}'),
             reply(self.proposal().model_dump_json())]
        )
        corpus = build_corpus_map(
            db, project_id=make_project(db, slug="repaired").id, snapshot_ids=()
        )

        result = draft_evidence_outline(
            model,
            corpus,
            tutorial_title="Gateway tutorial",
            reader_level="beginner",
            length_preset="standard",
        )

        assert result.repairs == 1

    def test_a_proposal_that_never_validates_stops_the_step(self, db: Session) -> None:
        model = FakeChatModel([reply("{}"), reply("{}"), reply("{}")])
        corpus = build_corpus_map(
            db, project_id=make_project(db, slug="hopeless").id, snapshot_ids=()
        )

        with pytest.raises(StructuredOutputInvalid):
            draft_evidence_outline(
                model,
                corpus,
                tutorial_title="Gateway tutorial",
                reader_level="beginner",
                length_preset="standard",
            )


class TestOutlineApproval:
    def proposal(self) -> OutlineProposal:
        return OutlineProposal(
            title="Gateway tutorial",
            summary="A tour of the gateway.",
            chapters=(
                PLAN,
                OutlineChapterProposal(
                    slug="operations",
                    title="Operating it",
                    intent="Explain day two.",
                    questions=("What breaks?",),
                ),
            ),
        )

    def test_the_reviewer_is_shown_the_chapters_and_their_questions(self) -> None:
        payload = outline_approval_payload(self.proposal())

        assert payload["title"] == "Gateway tutorial"
        assert payload["chapters"][0]["slug"] == "deployment"
        assert payload["chapters"][0]["questions"] == ["How is the gateway supervised?"]

    def test_approval_produces_the_outline_the_chapters_are_written_from(self) -> None:
        decision = apply_outline_decision(
            self.proposal(), decision=ApprovalDecision.APPROVED, version=2
        )

        assert decision.approved
        assert decision.outline is not None
        assert [chapter.slug for chapter in decision.outline.chapters] == [
            "deployment",
            "operations",
        ]
        assert decision.outline.version == 2

    def test_a_reviewer_can_drop_and_reorder_chapters(self) -> None:
        decision = apply_outline_decision(
            self.proposal(),
            decision=ApprovalDecision.APPROVED,
            chapter_slugs=("operations",),
        )

        assert decision.outline is not None
        assert [chapter.slug for chapter in decision.outline.chapters] == ["operations"]

    def test_a_reviewer_can_retitle_a_chapter(self) -> None:
        decision = apply_outline_decision(
            self.proposal(),
            decision=ApprovalDecision.APPROVED,
            titles={"deployment": "Getting it deployed"},
        )

        assert decision.outline is not None
        assert decision.outline.chapters[0].title == "Getting it deployed"

    def test_an_unknown_slug_in_an_edit_is_refused(self) -> None:
        with pytest.raises(ValueError):
            apply_outline_decision(
                self.proposal(),
                decision=ApprovalDecision.APPROVED,
                chapter_slugs=("nope",),
            )

    def test_approving_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError):
            apply_outline_decision(
                self.proposal(), decision=ApprovalDecision.APPROVED, chapter_slugs=()
            )

    def test_requested_changes_send_the_note_back_without_an_outline(self) -> None:
        decision = apply_outline_decision(
            self.proposal(),
            decision=ApprovalDecision.CHANGES_REQUESTED,
            note="Split the deployment chapter.",
        )

        assert not decision.approved
        assert decision.outline is None
        assert decision.note == "Split the deployment chapter."

    def test_a_rejection_keeps_no_outline(self) -> None:
        decision = apply_outline_decision(self.proposal(), decision=ApprovalDecision.REJECTED)

        assert not decision.approved
        assert decision.outline is None

    def test_a_pending_decision_is_not_a_decision(self) -> None:
        with pytest.raises(ValueError):
            apply_outline_decision(self.proposal(), decision=ApprovalDecision.PENDING)


class TestChapterEvidence:
    def test_the_pack_request_is_derived_from_the_approved_chapter(self) -> None:
        search = FakeSearchService(default=[search_hit()])
        builder = EvidencePackBuilder(search, snapshots=SCOPE)

        built = build_chapter_evidence(
            builder, PLAN, project_id="project-1", snapshot_ids=tuple(SCOPE)
        )

        assert built.chapter_slug == "deployment"
        assert "How is the gateway supervised?" in search.query_texts
        assert "build_gateway" in search.query_texts


class TestChapterGeneration:
    def test_a_grounded_draft_becomes_a_chapter(self) -> None:
        model = FakeChatModel([reply(draft().model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0)

        assert generated.chapter.slug == "deployment"
        assert generated.chapter.ordinal == 0
        assert generated.chapter.facts[0].citation_ids == ("e1",)
        assert generated.audit.is_clean
        assert generated.rejected == ()

    def test_only_the_citations_the_chapter_used_are_carried_forward(self) -> None:
        built = pack(
            search_hit(locator="https://docs.example.test/a", document_id="d1", score=2.0),
            search_hit(locator="https://docs.example.test/b", document_id="d2", score=1.0),
        )
        model = FakeChatModel([reply(draft().model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=built, ordinal=0)

        assert [citation.id for citation in generated.citations] == ["e1"]

    def test_a_fact_citing_evidence_that_was_never_retrieved_is_dropped(self) -> None:
        invented = draft(
            facts=(
                AtomicFact(
                    id="f1",
                    statement="The gateway ships with a web console.",
                    kind=ClaimKind.FACT,
                    citation_ids=("e42",),
                ),
            )
        )
        model = FakeChatModel([reply(invented.model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0)

        assert generated.chapter.facts == ()
        assert generated.rejected[0].reason is RejectionReason.UNKNOWN_CITATION

    def test_a_teaching_abstraction_survives_without_evidence(self) -> None:
        analogy = draft(
            facts=(
                AtomicFact(
                    id="f2",
                    statement="Think of the supervisor as a night watchman.",
                    kind=ClaimKind.TEACHING_ABSTRACTION,
                    citation_ids=(),
                ),
            )
        )
        model = FakeChatModel([reply(analogy.model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0)

        assert generated.chapter.facts[0].kind is ClaimKind.TEACHING_ABSTRACTION

    def test_code_that_cannot_be_traced_is_relabelled_illustrative(self) -> None:
        untraceable = draft(
            blocks=(
                CodeBlock(
                    language="python",
                    code="gateway = build_gateway(config)",
                    citation_ids=("e42",),
                ),
            )
        )
        model = FakeChatModel([reply(untraceable.model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0)

        block = generated.chapter.blocks[0]
        assert isinstance(block, CodeBlock)
        assert block.illustrative
        assert block.citation_ids == ()

    def test_prose_keeps_its_text_with_the_invented_reference_removed(self) -> None:
        model = FakeChatModel(
            [
                reply(
                    draft(
                        blocks=(
                            MarkdownBlock(
                                markdown="The gateway runs behind a supervisor.",
                                citation_ids=("e1", "e42"),
                            ),
                        )
                    ).model_dump_json()
                )
            ]
        )

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0)

        block = generated.chapter.blocks[0]
        assert isinstance(block, MarkdownBlock)
        assert block.citation_ids == ("e1",)

    def test_a_chapter_with_no_evidence_is_refused_before_a_call_is_paid_for(self) -> None:
        empty = EvidencePackBuilder(FakeSearchService(), snapshots=SCOPE).build(request_for())
        model = FakeChatModel([reply(draft().model_dump_json())])

        with pytest.raises(EvidenceMissing):
            generate_chapter(model, plan=PLAN, pack=empty, ordinal=0)

        assert model.calls == 0

    def test_generation_is_charged_to_the_run_ledger(self) -> None:
        ledger = UsageLedger()
        model = FakeChatModel([reply(draft().model_dump_json())])

        generated = generate_chapter(model, plan=PLAN, pack=pack(), ordinal=0, ledger=ledger)

        assert ledger.step_fields()["model"] == STRUCTURED_SPEC.name
        assert generated.result.prompt_hash == model.requests[0].prompt_hash()


class TestAssembly:
    def two_chapters(self):
        first_pack = pack(search_hit(locator="https://docs.example.test/a", document_id="d1"))
        second_pack = pack(search_hit(locator="https://docs.example.test/b", document_id="d2"))
        first = generate_chapter(
            FakeChatModel([reply(draft().model_dump_json())]),
            plan=PLAN,
            pack=first_pack,
            ordinal=0,
        )
        second_plan = OutlineChapterProposal(
            slug="operations",
            title="Operating it",
            intent="Explain day two.",
            questions=("What breaks?",),
        )
        second = generate_chapter(
            FakeChatModel([reply(draft(title="Operating it").model_dump_json())]),
            plan=second_plan,
            pack=second_pack,
            ordinal=1,
        )
        return first, second

    def test_per_chapter_citation_ids_are_renumbered_document_wide(self) -> None:
        first, second = self.two_chapters()

        built = assemble_document(metadata_for(), (first, second))

        locators = {citation.id: citation.locator for citation in built.citations}
        assert len(locators) == 2
        assert built.chapters[0].facts[0].citation_ids != built.chapters[1].facts[0].citation_ids
        first_id = built.chapters[0].facts[0].citation_ids[0]
        assert locators[first_id] == "https://docs.example.test/a"

    def test_the_same_source_cited_by_two_chapters_becomes_one_citation(self) -> None:
        shared = pack(search_hit())
        chapters = tuple(
            generate_chapter(
                FakeChatModel([reply(draft().model_dump_json())]),
                plan=PLAN.model_copy(update={"slug": slug}),
                pack=shared,
                ordinal=ordinal,
            )
            for ordinal, slug in enumerate(("deployment", "operations"))
        )

        built = assemble_document(metadata_for(), chapters)

        assert len(built.citations) == 1

    def test_ordinals_follow_the_approved_order(self) -> None:
        first, second = self.two_chapters()

        built = assemble_document(metadata_for(), (first, second))

        assert [chapter.ordinal for chapter in built.chapters] == [0, 1]

    def test_the_assembled_document_has_a_stable_digest(self) -> None:
        first, second = self.two_chapters()

        assert (
            assemble_document(metadata_for(), (first, second)).content_digest()
            == assemble_document(metadata_for(), (first, second)).content_digest()
        )


class TestConsistencyRevision:
    def document(self):
        built = pack(search_hit())
        generated = generate_chapter(
            FakeChatModel([reply(
                draft(
                    blocks=(
                        MarkdownBlock(
                            markdown="The gateway sits behind the daemon.",
                            citation_ids=("e1",),
                        ),
                        CodeBlock(
                            language="python",
                            code="daemon = build_gateway(config)",
                            citation_ids=("e1",),
                        ),
                    ),
                    summary="The daemon supervises the gateway.",
                ).model_dump_json()
            )]),
            plan=PLAN,
            pack=built,
            ordinal=0,
        )
        return assemble_document(metadata_for(), (generated,))

    def revision(self, **overrides: object) -> ConsistencyRevision:
        payload: dict[str, object] = {
            "terminology": ({"term": "daemon", "canonical": "supervisor"},),
            "notes": "Unified the term for the supervising process.",
        }
        payload.update(overrides)
        return ConsistencyRevision(**payload)  # type: ignore[arg-type]

    def test_terminology_is_unified_in_prose_and_summaries(self) -> None:
        model = FakeChatModel([reply(self.revision().model_dump_json())])

        revised = revise_for_consistency(model, self.document())

        block = revised.document.chapters[0].blocks[0]
        assert isinstance(block, MarkdownBlock)
        assert "supervisor" in block.markdown
        assert "daemon" not in block.markdown
        assert "daemon" not in revised.document.chapters[0].summary

    def test_code_is_never_rewritten_by_a_terminology_pass(self) -> None:
        """Renaming an identifier in a sample would make it stop matching its source."""
        model = FakeChatModel([reply(self.revision().model_dump_json())])

        revised = revise_for_consistency(model, self.document())

        code = revised.document.chapters[0].blocks[1]
        assert isinstance(code, CodeBlock)
        assert code.code == "daemon = build_gateway(config)"

    def test_a_duplicate_fact_can_be_dropped(self) -> None:
        model = FakeChatModel(
            [reply(self.revision(drop_fact_ids=("f1",)).model_dump_json())]
        )

        revised = revise_for_consistency(model, self.document())

        assert revised.document.chapters[0].facts == ()

    def test_a_chapter_summary_can_be_rewritten(self) -> None:
        model = FakeChatModel(
            [
                reply(
                    self.revision(
                        chapter_summaries=(
                            {"slug": "deployment", "summary": "Deployment, end to end."},
                        )
                    ).model_dump_json()
                )
            ]
        )

        revised = revise_for_consistency(model, self.document())

        assert revised.document.chapters[0].summary == "Deployment, end to end."

    def test_a_glossary_addition_citing_unknown_evidence_is_dropped(self) -> None:
        model = FakeChatModel(
            [
                reply(
                    self.revision(
                        glossary=(
                            {
                                "term": "supervisor",
                                "definition": "Starts and restarts the gateway.",
                                "citation_ids": ("e99",),
                            },
                        )
                    ).model_dump_json()
                )
            ]
        )

        revised = revise_for_consistency(model, self.document())

        assert revised.document.glossary == ()
        assert revised.dropped

    def test_a_glossary_addition_with_real_evidence_is_kept(self) -> None:
        built = self.document()
        known = built.citations[0].id
        model = FakeChatModel(
            [
                reply(
                    self.revision(
                        glossary=(
                            {
                                "term": "supervisor",
                                "definition": "Starts and restarts the gateway.",
                                "citation_ids": (known,),
                            },
                        )
                    ).model_dump_json()
                )
            ]
        )

        revised = revise_for_consistency(model, built)

        assert revised.document.glossary[0].term == "supervisor"

    def test_a_revision_has_no_way_to_add_a_fact(self) -> None:
        """The pass tightens wording; new claims would arrive without evidence."""
        assert "facts" not in ConsistencyRevision.model_fields
        assert "blocks" not in ConsistencyRevision.model_fields

    def test_the_citations_of_a_revised_document_are_unchanged(self) -> None:
        original = self.document()
        model = FakeChatModel([reply(self.revision().model_dump_json())])

        revised = revise_for_consistency(model, original)

        assert revised.document.citations == original.citations
