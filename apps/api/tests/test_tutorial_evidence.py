"""Assembling a chapter's evidence pack.

Retrieval returns ranked hits; a pack is what a chapter is allowed to talk about. The
difference is the editorial work done here: run the queries a chapter actually implies,
boost an exact code symbol over a passing mention of the same word, prefer the paths the
outline pointed at, keep one page from crowding out the rest, and stay inside a size the
model can attend to and a reviewer can read.

Everything a pack contains must be citable, so a hit from a snapshot outside the approved
scope is dropped rather than quietly cited.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from conftest import (
    SeededCorpus,
    make_document,
    make_project,
    make_snapshot,
    make_symbol,
    seed_corpus,
)
from sqlalchemy.orm import Session
from tutorial_support import SNAPSHOT_ID, FakeSearchService, search_hit

from app.db.models.enums import CitationKind, DocumentKind, SourceKind
from app.retrieval import SearchBackend, SearchKind, build_search_service
from app.tutorial.evidence import (
    DEFAULT_CHAR_BUDGET,
    EvidenceItem,
    EvidencePackBuilder,
    EvidenceRequest,
    SnapshotRef,
    snapshot_refs,
)

WEB_SNAPSHOT = SnapshotRef(
    snapshot_id=SNAPSHOT_ID,
    kind=CitationKind.WEB,
    captured_at=datetime(2026, 8, 1, tzinfo=UTC),
)
REPO_SNAPSHOT = SnapshotRef(
    snapshot_id="snapshot-repo",
    kind=CitationKind.REPO,
    repo="octocat/hello-world",
    commit_sha="0" * 40,
)
SCOPE = {WEB_SNAPSHOT.snapshot_id: WEB_SNAPSHOT, REPO_SNAPSHOT.snapshot_id: REPO_SNAPSHOT}


def request_for(**overrides: object) -> EvidenceRequest:
    payload: dict[str, object] = {
        "project_id": "project-1",
        "chapter_slug": "deployment",
        "chapter_title": "Deploying the gateway",
        "questions": ("How is the gateway supervised?",),
        "snapshot_ids": tuple(SCOPE),
    }
    payload.update(overrides)
    return EvidenceRequest(**payload)  # type: ignore[arg-type]


def builder(search: FakeSearchService, **overrides: object) -> EvidencePackBuilder:
    return EvidencePackBuilder(search, snapshots=SCOPE, **overrides)  # type: ignore[arg-type]


class TestQueryDerivation:
    def test_the_chapter_title_and_every_question_are_searched(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        pack = builder(search).build(
            request_for(questions=("How is it supervised?", "Where do settings live?"))
        )

        assert "Deploying the gateway" in search.query_texts
        assert "How is it supervised?" in search.query_texts
        assert "Where do settings live?" in search.query_texts
        assert pack.queries == tuple(search.query_texts)

    def test_a_named_symbol_is_looked_up_in_the_code_index_only(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        builder(search).build(request_for(symbols=("build_gateway",)))

        symbol_queries = [query for query in search.queries if query.text == "build_gateway"]
        assert symbol_queries
        assert symbol_queries[0].kinds == (SearchKind.CODE_SYMBOL,)

    def test_every_query_is_confined_to_the_approved_snapshots(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        builder(search).build(request_for())

        assert all(query.snapshot_ids == tuple(SCOPE) for query in search.queries)

    def test_the_project_scope_is_carried_into_every_query(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        builder(search).build(request_for())

        assert {query.project_id for query in search.queries} == {"project-1"}


class TestMerging:
    def test_the_same_location_found_twice_appears_once(self) -> None:
        search = FakeSearchService(default=[search_hit(), search_hit()])

        pack = builder(search).build(request_for())

        assert len(pack.items) == 1

    def test_the_best_score_wins_when_a_location_is_found_by_two_queries(self) -> None:
        search = FakeSearchService(
            {
                "Deploying the gateway": [search_hit(score=1.0)],
                "How is the gateway supervised?": [search_hit(score=5.0)],
            }
        )

        pack = builder(search).build(request_for())

        assert pack.items[0].score >= 5.0

    def test_hits_come_back_in_descending_score_order(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(locator="https://docs.example.test/a", document_id="d1", score=1.0),
                search_hit(locator="https://docs.example.test/c", document_id="d3", score=3.0),
                search_hit(locator="https://docs.example.test/b", document_id="d2", score=2.0),
            ]
        )

        pack = builder(search).build(request_for())

        assert [item.score for item in pack.items] == sorted(
            (item.score for item in pack.items), reverse=True
        )

    def test_equal_scores_are_ordered_by_locator_so_a_pack_is_reproducible(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(locator="https://docs.example.test/b", document_id="d2", score=1.0),
                search_hit(locator="https://docs.example.test/a", document_id="d1", score=1.0),
            ]
        )

        pack = builder(search).build(request_for())

        assert [item.locator for item in pack.items] == [
            "https://docs.example.test/a",
            "https://docs.example.test/b",
        ]

    def test_one_page_cannot_crowd_out_the_rest_of_the_pack(self) -> None:
        crowd = [
            search_hit(locator=f"https://docs.example.test/deploy#chunk-{index}", score=9.0)
            for index in range(5)
        ]
        other = search_hit(
            locator="https://docs.example.test/configuration",
            document_id="document-2",
            score=1.0,
        )
        search = FakeSearchService(default=[*crowd, other])

        pack = builder(search).build(request_for(max_per_document=2))

        assert sum(1 for item in pack.items if item.document_id == "document-1") == 2
        assert any(item.document_id == "document-2" for item in pack.items)


class TestBoosting:
    def test_an_exact_symbol_match_outranks_a_prose_mention_of_the_same_word(self) -> None:
        prose = search_hit(
            locator="https://docs.example.test/glossary",
            title="Glossary",
            document_id="document-9",
            excerpt="build_gateway is mentioned in passing here.",
            score=2.0,
        )
        symbol = search_hit(
            kind=SearchKind.CODE_SYMBOL,
            snapshot_id=REPO_SNAPSHOT.snapshot_id,
            locator="src/gateway/factory.py#L10-L24",
            title="gateway.factory.build_gateway",
            excerpt="def build_gateway(config: Config) -> Gateway",
            document_id="document-3",
            chunk_id=None,
            symbol_id="symbol-1",
            score=2.0,
        )
        search = FakeSearchService(default=[prose, symbol])

        pack = builder(search).build(request_for(symbols=("build_gateway",)))

        assert pack.items[0].symbol_id == "symbol-1"
        assert any(reason.startswith("symbol:") for reason in pack.items[0].reasons)

    def test_a_path_the_outline_pointed_at_is_preferred(self) -> None:
        hinted = search_hit(
            locator="https://docs.example.test/deploy/gateway",
            title="Reference",
            document_id="document-4",
            score=1.0,
        )
        unhinted = search_hit(
            locator="https://docs.example.test/changelog",
            title="Changelog",
            document_id="document-5",
            score=1.0,
        )
        search = FakeSearchService(default=[hinted, unhinted])

        pack = builder(search).build(request_for(path_hints=("deploy/gateway",)))

        assert pack.items[0].locator.endswith("/deploy/gateway")
        assert "path:deploy/gateway" in pack.items[0].reasons

    def test_a_title_that_matches_the_chapter_is_preferred(self) -> None:
        on_topic = search_hit(
            title="Gateway deployment guide",
            locator="https://docs.example.test/deploy",
            document_id="document-6",
            score=1.0,
        )
        off_topic = search_hit(
            title="Changelog",
            locator="https://docs.example.test/changelog",
            document_id="document-7",
            score=1.0,
        )
        search = FakeSearchService(default=[on_topic, off_topic])

        pack = builder(search).build(request_for())

        assert pack.items[0].title == "Gateway deployment guide"
        assert any(reason.startswith("title:") for reason in pack.items[0].reasons)


class TestScope:
    def test_a_hit_from_outside_the_approved_scope_is_dropped(self) -> None:
        """Retrieval is asked to filter; the pack refuses to cite it either way."""
        search = FakeSearchService(default=[search_hit(snapshot_id="snapshot-unapproved")])

        pack = builder(search).build(request_for())

        assert pack.items == ()

    def test_a_hit_whose_snapshot_cannot_be_described_is_dropped(self) -> None:
        search = FakeSearchService(default=[search_hit(snapshot_id="snapshot-undescribed")])

        pack = builder(search).build(
            request_for(snapshot_ids=(SNAPSHOT_ID, "snapshot-undescribed"))
        )

        assert pack.items == ()


class TestBudget:
    def test_the_item_count_is_capped_and_the_pack_says_it_was_truncated(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(
                    locator=f"https://docs.example.test/page-{index}",
                    document_id=f"document-{index}",
                    score=float(index),
                )
                for index in range(10)
            ]
        )

        pack = builder(search).build(request_for(max_items=3))

        assert len(pack.items) == 3
        assert pack.truncated

    def test_the_character_budget_stops_the_pack_growing(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(
                    locator=f"https://docs.example.test/page-{index}",
                    document_id=f"document-{index}",
                    excerpt="x" * 300,
                    score=float(10 - index),
                )
                for index in range(10)
            ]
        )

        pack = builder(search).build(request_for(char_budget=1_000))

        assert sum(len(item.excerpt) for item in pack.items) <= 1_000
        assert pack.truncated

    def test_a_pack_that_fits_is_not_marked_truncated(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        pack = builder(search).build(request_for())

        assert not pack.truncated
        assert DEFAULT_CHAR_BUDGET > 0

    def test_a_chapter_with_no_evidence_produces_an_empty_pack_rather_than_an_error(self) -> None:
        pack = builder(FakeSearchService()).build(request_for())

        assert pack.is_empty
        assert pack.chapter_slug == "deployment"


class TestCitations:
    def test_items_get_stable_citation_ids_in_pack_order(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(locator="https://docs.example.test/a", document_id="d1", score=2.0),
                search_hit(locator="https://docs.example.test/b", document_id="d2", score=1.0),
            ]
        )

        pack = builder(search).build(request_for())

        assert pack.citation_ids() == ("e1", "e2")
        assert pack.by_citation_id()["e1"].locator == "https://docs.example.test/a"

    def test_a_web_item_becomes_a_citation_the_document_model_accepts(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        citations = builder(search).build(request_for()).to_citations()

        assert citations[0].kind is CitationKind.WEB
        assert citations[0].locator == search_hit().locator
        assert citations[0].retrieved_at == WEB_SNAPSHOT.captured_at

    def test_a_repository_item_is_pinned_to_the_commit_it_was_read_at(self) -> None:
        search = FakeSearchService(
            default=[
                search_hit(
                    kind=SearchKind.CODE_SYMBOL,
                    snapshot_id=REPO_SNAPSHOT.snapshot_id,
                    locator="src/gateway/factory.py#L10-L24",
                    title="gateway.factory.build_gateway",
                    symbol_id="symbol-1",
                    chunk_id=None,
                )
            ]
        )

        citation = builder(search).build(request_for()).to_citations()[0]

        assert citation.kind is CitationKind.REPO
        assert citation.locator == f"octocat/hello-world@{'0' * 40}/src/gateway/factory.py#L10-L24"
        assert citation.symbol_id == "symbol-1"

    def test_the_quote_carried_into_a_citation_is_the_retrieved_excerpt(self) -> None:
        search = FakeSearchService(default=[search_hit(excerpt="Deploy the gateway service.")])

        citation = builder(search).build(request_for()).to_citations()[0]

        assert citation.quote == "Deploy the gateway service."

    def test_the_pack_can_report_every_locator_it_permits(self) -> None:
        search = FakeSearchService(default=[search_hit()])

        pack = builder(search).build(request_for())

        assert pack.locators() == frozenset({search_hit().locator})


class TestRerankerHook:
    def test_a_reranker_can_reorder_the_pack(self) -> None:
        """The seam a hybrid keyword-plus-vector stage plugs into later."""

        class ReverseReranker:
            def rerank(
                self,
                request: EvidenceRequest,
                items: Sequence[EvidenceItem],
            ) -> Sequence[EvidenceItem]:
                return list(reversed(items))

        search = FakeSearchService(
            default=[
                search_hit(locator="https://docs.example.test/a", document_id="d1", score=2.0),
                search_hit(locator="https://docs.example.test/b", document_id="d2", score=1.0),
            ]
        )

        pack = builder(search, reranker=ReverseReranker()).build(request_for())

        assert [item.locator for item in pack.items] == [
            "https://docs.example.test/b",
            "https://docs.example.test/a",
        ]


class TestAgainstTheRealRetrievalService:
    """The builder has to work with the retrieval implementation, not only with a fake."""

    def test_a_pack_is_assembled_from_the_indexed_corpus(self, db: Session) -> None:
        corpus: SeededCorpus = seed_corpus(db)
        scope = snapshot_refs(db, (corpus.primary_snapshot.id,))
        search = build_search_service(db, backend=SearchBackend.PORTABLE_LIKE)

        pack = EvidencePackBuilder(search, snapshots=scope).build(
            EvidenceRequest(
                project_id=corpus.project.id,
                chapter_slug="deployment",
                chapter_title="Gateway deployment",
                questions=("How is the gateway supervised?",),
                snapshot_ids=(corpus.primary_snapshot.id,),
            )
        )

        assert pack.items
        assert {item.snapshot_id for item in pack.items} == {corpus.primary_snapshot.id}
        assert all(item.excerpt for item in pack.items)
        assert pack.to_citations()

    def test_snapshot_references_describe_a_repository_snapshot(self, db: Session) -> None:
        project = make_project(db, slug="repo-project")
        snapshot = make_snapshot(db, project=project, locator="octocat/hello-world")
        snapshot.commit_sha = "a" * 40
        document = make_document(
            db,
            snapshot=snapshot,
            kind=DocumentKind.REPO_FILE,
            path="src/gateway.py",
            uri="https://github.test/octocat/hello-world/blob/main/src/gateway.py",
        )
        make_symbol(db, document=document)
        _mark_repository_source(db, snapshot.source_id)
        db.commit()

        references = snapshot_refs(db, (snapshot.id,))

        assert references[snapshot.id].kind is CitationKind.REPO
        assert references[snapshot.id].repo == "octocat/hello-world"
        assert references[snapshot.id].commit_sha == "a" * 40

    def test_an_unknown_snapshot_id_is_simply_absent(self, db: Session) -> None:
        assert snapshot_refs(db, ("nope",)) == {}


def _mark_repository_source(db: Session, source_id: str) -> None:
    from app.db.models import Source

    source = db.get(Source, source_id)
    assert source is not None
    source.kind = SourceKind.GITHUB_REPO
    db.flush()


class TestRequestValidation:
    def test_a_request_needs_something_to_search_for(self) -> None:
        with pytest.raises(ValueError):
            EvidenceRequest(
                project_id="project-1",
                chapter_slug="deployment",
                chapter_title="   ",
                snapshot_ids=(SNAPSHOT_ID,),
            )

    def test_a_request_needs_an_approved_snapshot_scope(self) -> None:
        """Evidence that is not pinned to an approved snapshot cannot be cited later."""
        with pytest.raises(ValueError):
            request_for(snapshot_ids=())
