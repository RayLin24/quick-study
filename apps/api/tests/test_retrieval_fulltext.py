"""Retrieval interface and its two implementations.

The interface tests run on both backends. The MySQL-only tests additionally prove the
FULLTEXT behaviour the production implementation depends on and skip without
``QUICKSTUDY_TEST_MYSQL_URL``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from conftest import SeededCorpus, seed_corpus
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.retrieval import (
    SearchBackend,
    SearchKind,
    SearchQuery,
    SearchService,
    UnsupportedSearchBackend,
    build_search_service,
    normalise_terms,
)
from app.retrieval.fulltext import MySQLFullTextSearchService, PortableLikeSearchService


@pytest.fixture
def corpus(db: Session) -> SeededCorpus:
    return seed_corpus(db)


@pytest.fixture
def service(db: Session) -> SearchService:
    return build_search_service(db, backend=SearchBackend.PORTABLE_LIKE)


@pytest.fixture
def mysql_db(migrated_mysql_engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=migrated_mysql_engine, expire_on_commit=False, future=True)
    with factory() as session:
        yield session


@pytest.fixture
def mysql_corpus(mysql_db: Session) -> SeededCorpus:
    return seed_corpus(mysql_db)


@pytest.fixture
def mysql_service(mysql_db: Session) -> SearchService:
    return build_search_service(mysql_db)


def test_terms_are_extracted_without_full_text_operators() -> None:
    assert normalise_terms("gateway supervisor") == ("gateway", "supervisor")
    assert normalise_terms('+gateway* -"supervisor" @distance') == (
        "gateway",
        "supervisor",
        "distance",
    )
    assert normalise_terms("   ") == ()
    assert normalise_terms("+-*()~<>") == ()


def test_latin_terms_shorter_than_the_index_token_size_are_dropped() -> None:
    """One and two letter words are noise, and searching them is misleading."""
    assert normalise_terms("a an gateway") == ("gateway",)


def test_a_two_character_chinese_word_is_a_usable_term() -> None:
    """The ngram index tokenises Chinese in pairs, so two characters are a real word."""
    assert normalise_terms("网关") == ("网关",)
    assert normalise_terms("部署 网关 服务") == ("部署", "网关", "服务")


def test_the_factory_refuses_to_downgrade_a_deployment_without_being_asked(
    db: Session,
) -> None:
    """A substring scan that silently replaces search is worse than a startup failure."""
    with pytest.raises(UnsupportedSearchBackend):
        build_search_service(db)


def test_the_portable_implementation_can_still_be_asked_for_by_name(db: Session) -> None:
    service = build_search_service(db, backend=SearchBackend.PORTABLE_LIKE)

    assert isinstance(service, PortableLikeSearchService)


def test_the_factory_chooses_the_mysql_implementation_on_mysql(mysql_db: Session) -> None:
    assert isinstance(build_search_service(mysql_db), MySQLFullTextSearchService)


def test_an_empty_query_returns_nothing(service: SearchService, corpus: SeededCorpus) -> None:
    assert service.search(SearchQuery(project_id=corpus.project.id, text="   ")) == []


def test_a_query_of_only_operators_returns_nothing(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    assert service.search(SearchQuery(project_id=corpus.project.id, text="+*~()")) == []


def test_searching_finds_the_documents_that_mention_a_term(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(SearchQuery(project_id=corpus.project.id, text="deployment"))

    assert corpus.deployment_document.id in {hit.document_id for hit in hits}


def test_results_never_cross_a_project_boundary(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(SearchQuery(project_id=corpus.project.id, text="gateway"))

    assert hits
    assert corpus.other_project_document.id not in {hit.document_id for hit in hits}
    assert {hit.project_id for hit in hits} == {corpus.project.id}


def test_results_can_be_restricted_to_the_approved_snapshots(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="supervisor",
            snapshot_ids=(corpus.primary_snapshot.id,),
        )
    )

    assert hits
    assert {hit.snapshot_id for hit in hits} == {corpus.primary_snapshot.id}
    assert corpus.legacy_document.id not in {hit.document_id for hit in hits}


def test_a_title_match_outranks_the_same_term_in_a_body(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="deployment",
            kinds=(SearchKind.DOCUMENT_TITLE, SearchKind.DOCUMENT_BODY),
        )
    )

    assert hits[0].kind is SearchKind.DOCUMENT_TITLE


def test_hits_come_back_ordered_by_descending_score(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(SearchQuery(project_id=corpus.project.id, text="gateway supervisor"))

    assert hits
    assert [hit.score for hit in hits] == sorted((hit.score for hit in hits), reverse=True)


def test_only_the_requested_kinds_are_searched(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="gateway",
            kinds=(SearchKind.CODE_SYMBOL,),
        )
    )

    assert hits
    assert {hit.kind for hit in hits} == {SearchKind.CODE_SYMBOL}


def test_a_code_symbol_is_found_by_its_qualified_name(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="build_gateway",
            kinds=(SearchKind.CODE_SYMBOL,),
        )
    )

    assert [hit.symbol_id for hit in hits] == [corpus.symbol.id]
    assert hits[0].locator == "src/gateway/factory.py#L10-L24"


def test_chunk_hits_carry_a_citable_locator(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="supervisor",
            kinds=(SearchKind.DOCUMENT_CHUNK,),
        )
    )

    assert hits
    assert all(hit.chunk_id for hit in hits)
    assert hits[0].locator.startswith("https://")
    assert "#chunk-" in hits[0].locator


def test_excerpts_are_bounded_so_an_evidence_pack_stays_reviewable(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    from app.retrieval.service import EXCERPT_MAX_LENGTH

    corpus.deployment_document.body_text = "supervisor " * 500
    hits = service.search(
        SearchQuery(
            project_id=corpus.project.id,
            text="supervisor",
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert hits
    assert all(len(hit.excerpt) <= EXCERPT_MAX_LENGTH for hit in hits)


def test_the_limit_caps_the_number_of_hits(
    service: SearchService,
    corpus: SeededCorpus,
) -> None:
    hits = service.search(
        SearchQuery(project_id=corpus.project.id, text="gateway supervisor", limit=2)
    )

    assert len(hits) == 2


def test_mysql_full_text_search_finds_a_body_term(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="rewrite",
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert [hit.document_id for hit in hits] == [mysql_corpus.legacy_document.id]
    assert hits[0].score > 0


def test_mysql_finds_a_term_that_appears_in_most_documents(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    """Natural-language mode would silently drop ``supervisor`` as too common."""
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="supervisor",
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert {hit.document_id for hit in hits} == {
        mysql_corpus.deployment_document.id,
        mysql_corpus.configuration_document.id,
        mysql_corpus.legacy_document.id,
    }


def test_mysql_survives_a_query_full_of_boolean_operators(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    """Unsanitised operators make MySQL raise a boolean full-text syntax error."""
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text='+++gateway*** ---"supervisor)) @@ ~~',
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert hits


def test_mysql_scopes_full_text_results_to_the_project(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(project_id=mysql_corpus.project.id, text="gateway")
    )

    assert hits
    assert {hit.project_id for hit in hits} == {mysql_corpus.project.id}


def test_mysql_finds_a_code_symbol_through_the_identifier_index(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="build_gateway",
            kinds=(SearchKind.CODE_SYMBOL,),
        )
    )

    assert [hit.symbol_id for hit in hits] == [mysql_corpus.symbol.id]


def test_mysql_finds_a_chinese_term_inside_prose_that_has_no_word_breaks(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    """Projects default to Chinese output; the built-in parser finds none of this."""
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="部署网关",
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert [hit.document_id for hit in hits] == [mysql_corpus.chinese_document.id]


def test_mysql_finds_a_two_character_chinese_term(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="网关",
            kinds=(SearchKind.DOCUMENT_BODY,),
        )
    )

    assert [hit.document_id for hit in hits] == [mysql_corpus.chinese_document.id]


def test_mysql_finds_a_chinese_title(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="部署指南",
            kinds=(SearchKind.DOCUMENT_TITLE,),
        )
    )

    assert [hit.document_id for hit in hits] == [mysql_corpus.chinese_document.id]


def test_mysql_finds_a_chinese_chunk_so_a_citation_can_point_at_it(
    mysql_service: SearchService,
    mysql_corpus: SeededCorpus,
) -> None:
    hits = mysql_service.search(
        SearchQuery(
            project_id=mysql_corpus.project.id,
            text="反向代理",
            kinds=(SearchKind.DOCUMENT_CHUNK,),
        )
    )

    assert hits
    assert all(hit.chunk_id for hit in hits)
