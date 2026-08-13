"""Strict parsing of what a user typed into the "GitHub repository" box.

Everything downstream — the API paths, the citation prefix, the artifact provenance — is
built from this, so a lenient parser here is a request-forgery primitive. Only github.com
repositories are accepted, and only in shapes that cannot smuggle a path anywhere else.
"""

from __future__ import annotations

import pytest

from app.ingestion.github.refs import (
    RepositoryRef,
    RepositoryRefError,
    parse_repository,
)


@pytest.mark.parametrize(
    "spec",
    [
        "octo/gateway",
        "https://github.com/octo/gateway",
        "https://github.com/octo/gateway/",
        "https://github.com/octo/gateway.git",
        "http://github.com/octo/gateway",
        "https://www.github.com/octo/gateway",
        "github.com/octo/gateway",
        "git@github.com:octo/gateway.git",
        "  octo/gateway  ",
    ],
)
def test_every_accepted_spelling_resolves_to_the_same_repository(spec: str) -> None:
    assert parse_repository(spec) == RepositoryRef(owner="octo", name="gateway")


def test_the_full_name_is_what_api_paths_and_citations_use() -> None:
    reference = parse_repository("octo/gateway")

    assert reference.full_name == "octo/gateway"
    assert str(reference) == "octo/gateway"


def test_case_is_preserved_because_github_displays_it() -> None:
    assert parse_repository("Octo/Gateway").full_name == "Octo/Gateway"


def test_a_trailing_git_suffix_is_only_stripped_once() -> None:
    assert parse_repository("octo/gateway.git.git").name == "gateway.git"


@pytest.mark.parametrize(
    "spec",
    [
        "https://gitlab.com/octo/gateway",
        "https://evil.test/github.com/octo/gateway",
        "https://github.com.evil.test/octo/gateway",
        "https://notgithub.com/octo/gateway",
        "git@gitlab.com:octo/gateway.git",
    ],
)
def test_a_repository_somewhere_other_than_github_is_refused(spec: str) -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "https://github.com/octo/gateway/tree/main",
        "https://github.com/octo/gateway/blob/main/src/app.py",
        "https://github.com/octo",
        "https://github.com/",
        "octo/gateway/extra",
        "octo",
        "/octo/gateway",
        "octo//gateway",
    ],
)
def test_anything_that_is_not_exactly_a_repository_is_refused(spec: str) -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "../../etc/passwd",
        "octo/../secrets",
        "octo/.",
        "octo/..",
        "./gateway",
        "octo/gate way",
        "octo/gateway?x=1",
        "octo/gateway#frag",
        "octo/gateway%2F..%2Fadmin",
        "octo\\gateway",
    ],
)
def test_a_specification_carrying_path_or_query_syntax_is_refused(spec: str) -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository(spec)


@pytest.mark.parametrize(
    "spec",
    ["-octo/gateway", "octo-/gateway", "o" * 40 + "/gateway", "octo/" + "g" * 101],
)
def test_names_github_itself_would_reject_are_refused(spec: str) -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository(spec)


def test_credentials_in_a_repository_url_are_refused() -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository("https://token@github.com/octo/gateway")


@pytest.mark.parametrize("spec", ["", "   ", None, 42])
def test_input_that_is_not_a_specification_at_all_is_refused(spec: object) -> None:
    with pytest.raises(RepositoryRefError):
        parse_repository(spec)  # type: ignore[arg-type]


def test_a_reference_is_hashable_so_it_can_key_a_cache() -> None:
    assert len({parse_repository("octo/gateway"), parse_repository("octo/gateway")}) == 1
