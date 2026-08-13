"""The two reference formats every tutorial claim must resolve to.

A citation is only worth anything if it is checkable. Both formats therefore pin the exact
revision of what was read — a fetch instant for a page, a commit SHA for a file — and both
round-trip, so the quality gate can parse a reference back out of generated Markdown and
go looking for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.ingestion.citations import (
    CitationError,
    RepoCitation,
    WebCitation,
    format_repo_citation,
    format_web_citation,
    parse_citation,
)

FETCHED_AT = datetime(2026, 3, 4, 10, 30, 0, tzinfo=UTC)
COMMIT = "9f2c1b7a4e5d6f80123456789abcdef012345678"


class TestWebCitation:
    def test_it_pins_the_page_the_chunk_and_the_moment_it_was_read(self) -> None:
        reference = format_web_citation(
            "https://docs.example.test/guide/install", FETCHED_AT, "requirements"
        )

        assert reference == (
            "https://docs.example.test/guide/install#requirements@2026-03-04T10:30:00Z"
        )

    def test_it_round_trips(self) -> None:
        citation = WebCitation(
            url="https://docs.example.test/guide/install",
            fetched_at=FETCHED_AT,
            chunk_id="requirements",
        )

        assert parse_citation(citation.reference) == citation

    def test_a_url_carrying_a_query_still_round_trips(self) -> None:
        citation = WebCitation(
            url="https://docs.example.test/search?q=install&page=2",
            fetched_at=FETCHED_AT,
            chunk_id="results",
        )

        assert parse_citation(citation.reference) == citation

    def test_the_instant_is_recorded_in_utc_whatever_it_was_given_in(self) -> None:
        local = FETCHED_AT.astimezone(timezone(timedelta(hours=8)))

        reference = format_web_citation("https://docs.example.test/a", local, "top")

        assert reference.endswith("@2026-03-04T10:30:00Z")

    def test_an_instant_without_a_timezone_is_refused(self) -> None:
        """A naive timestamp cannot identify a moment, so it cannot pin a snapshot."""
        with pytest.raises(CitationError):
            format_web_citation("https://docs.example.test/a", datetime(2026, 3, 4, 10), "top")

    def test_a_non_http_url_is_refused(self) -> None:
        with pytest.raises(CitationError):
            format_web_citation("file:///etc/passwd", FETCHED_AT, "top")

    @pytest.mark.parametrize("chunk_id", ["", "has space", "has#hash", "has@at"])
    def test_a_chunk_id_that_would_break_parsing_is_refused(self, chunk_id: str) -> None:
        with pytest.raises(CitationError):
            format_web_citation("https://docs.example.test/a", FETCHED_AT, chunk_id)


class TestRepoCitation:
    def test_it_uses_the_pinned_commit_and_a_line_range(self) -> None:
        reference = format_repo_citation("octo/gateway", COMMIT, "src/app.py", 10, 24)

        assert reference == f"octo/gateway@{COMMIT}/src/app.py#L10-L24"

    def test_a_single_line_needs_no_range(self) -> None:
        assert format_repo_citation("octo/gateway", COMMIT, "src/app.py", 10) == (
            f"octo/gateway@{COMMIT}/src/app.py#L10"
        )

    def test_a_range_that_starts_and_ends_on_one_line_collapses(self) -> None:
        assert format_repo_citation("octo/gateway", COMMIT, "src/app.py", 10, 10) == (
            f"octo/gateway@{COMMIT}/src/app.py#L10"
        )

    def test_it_round_trips(self) -> None:
        citation = RepoCitation(
            repo="octo/gateway", commit=COMMIT, path="src/app.py", start_line=10, end_line=24
        )

        assert parse_citation(citation.reference) == citation

    def test_a_single_line_citation_round_trips(self) -> None:
        citation = RepoCitation(
            repo="octo/gateway", commit=COMMIT, path="src/app.py", start_line=10
        )

        parsed = parse_citation(citation.reference)

        assert parsed == citation
        assert isinstance(parsed, RepoCitation)
        assert parsed.end_line is None

    def test_a_branch_name_is_refused_because_it_is_not_a_fixed_revision(self) -> None:
        with pytest.raises(CitationError):
            format_repo_citation("octo/gateway", "main", "src/app.py", 1)

    def test_an_abbreviated_sha_is_refused(self) -> None:
        with pytest.raises(CitationError):
            format_repo_citation("octo/gateway", COMMIT[:7], "src/app.py", 1)

    @pytest.mark.parametrize(
        "path", ["../etc/passwd", "/etc/passwd", "src/../../etc/passwd", "", "src\\app.py"]
    )
    def test_a_path_that_leaves_the_repository_is_refused(self, path: str) -> None:
        with pytest.raises(CitationError):
            format_repo_citation("octo/gateway", COMMIT, path, 1)

    def test_a_malformed_repository_name_is_refused(self) -> None:
        with pytest.raises(CitationError):
            format_repo_citation("not-a-repo", COMMIT, "src/app.py", 1)

    @pytest.mark.parametrize(("start", "end"), [(0, None), (-1, None), (10, 2)])
    def test_impossible_line_numbers_are_refused(self, start: int, end: int | None) -> None:
        with pytest.raises(CitationError):
            format_repo_citation("octo/gateway", COMMIT, "src/app.py", start, end)

    def test_a_path_with_a_hash_in_it_still_parses(self) -> None:
        citation = RepoCitation(
            repo="octo/gateway", commit=COMMIT, path="docs/c#-notes.md", start_line=3
        )

        assert parse_citation(citation.reference) == citation


class TestParsing:
    @pytest.mark.parametrize(
        "text",
        [
            "",
            "just some prose",
            "https://docs.example.test/a",
            "octo/gateway@main/src/app.py#L1",
            "octo/gateway@" + COMMIT + "/src/app.py",
            "https://docs.example.test/a#top@not-a-date",
        ],
    )
    def test_anything_that_is_not_a_citation_is_refused(self, text: str) -> None:
        with pytest.raises(CitationError):
            parse_citation(text)

    def test_the_two_formats_are_told_apart_without_a_prefix(self) -> None:
        web = parse_citation("https://docs.example.test/a#top@2026-03-04T10:30:00Z")
        repo = parse_citation(f"octo/gateway@{COMMIT}/src/app.py#L1-L2")

        assert isinstance(web, WebCitation)
        assert isinstance(repo, RepoCitation)
