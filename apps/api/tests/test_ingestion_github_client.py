"""Reading a repository through the API, pinned to one commit.

The behaviour that matters most is the truncation fallback. GitHub's recursive tree
endpoint silently returns a *partial* answer for large repositories with
``"truncated": true``, and a client that misses that flag analyses a fraction of the
repository while reporting success.
"""

from __future__ import annotations

import base64
import json

import pytest
from ingestion_support import StubSite, public_resolver

from app.ingestion.github.client import (
    GitHubClient,
    GitHubError,
    RepositoryNotFound,
    RepositoryNotPublic,
    TruncatedTree,
)
from app.ingestion.github.refs import parse_repository
from app.ingestion.github.tree import TreeLimits, collect_files
from app.ingestion.web.fetcher import FetchLimits, SafeFetcher
from app.ingestion.web.safety import AddressGuard

REF = parse_repository("octo/gateway")
COMMIT = "9f2c1b7a4e5d6f80123456789abcdef012345678"
ROOT_TREE = "1" * 40
SRC_TREE = "2" * 40
UTILS_TREE = "3" * 40


def api(path: str) -> str:
    return f"https://api.github.com{path}"


def entry(
    path: str,
    kind: str = "blob",
    sha: str = "a" * 40,
    size: int | None = 100,
    mode: str = "100644",
) -> dict:
    record = {"path": path, "mode": mode, "type": kind, "sha": sha}
    if size is not None and kind == "blob":
        record["size"] = size
    return record


@pytest.fixture
def site() -> StubSite:
    stub = StubSite()
    stub.add(
        api("/repos/octo/gateway"),
        json.dumps(
            {
                "full_name": "octo/gateway",
                "default_branch": "main",
                "private": False,
                "fork": False,
                "archived": False,
                "size": 512,
                "html_url": "https://github.com/octo/gateway",
            }
        ),
        content_type="application/json; charset=utf-8",
    )
    stub.add(
        api("/repos/octo/gateway/commits/main"),
        json.dumps({"sha": COMMIT, "commit": {"message": "release"}}),
        content_type="application/json; charset=utf-8",
    )
    return stub


def client_for(site: StubSite, *, token: str | None = None) -> GitHubClient:
    fetcher = SafeFetcher(
        guard=AddressGuard(resolver=public_resolver("api.github.com")),
        transport=site.transport,
        limits=FetchLimits(),
    )
    return GitHubClient(fetcher, token=token)


class TestRepository:
    def test_the_default_branch_is_read_from_the_api(self, site: StubSite) -> None:
        assert client_for(site).repository(REF).default_branch == "main"

    def test_a_repository_that_does_not_exist_is_reported_as_such(self, site: StubSite) -> None:
        site.add(
            api("/repos/octo/missing"),
            json.dumps({"message": "Not Found"}),
            status_code=404,
            content_type="application/json",
        )

        with pytest.raises(RepositoryNotFound):
            client_for(site).repository(parse_repository("octo/missing"))

    def test_a_private_repository_is_refused(self, site: StubSite) -> None:
        """The product boundary is public sources; a token must not widen it."""
        site.add(
            api("/repos/octo/secret"),
            json.dumps({"full_name": "octo/secret", "default_branch": "main", "private": True}),
            content_type="application/json",
        )

        with pytest.raises(RepositoryNotPublic):
            client_for(site).repository(parse_repository("octo/secret"))

    def test_a_token_is_sent_only_when_one_is_configured(self, site: StubSite) -> None:
        client_for(site).repository(REF)
        assert "authorization" not in site.requests[-1].headers

        client_for(site, token="ghp_example").repository(REF)
        assert site.requests[-1].headers["authorization"] == "Bearer ghp_example"

    def test_the_api_version_is_pinned(self, site: StubSite) -> None:
        client_for(site).repository(REF)

        assert site.requests[-1].headers["x-github-api-version"]
        assert "application/vnd.github+json" in site.requests[-1].headers["accept"]

    def test_being_rate_limited_is_a_distinct_failure(self, site: StubSite) -> None:
        site.add(
            api("/repos/octo/gateway"),
            json.dumps({"message": "API rate limit exceeded"}),
            status_code=403,
            headers={"x-ratelimit-remaining": "0"},
            content_type="application/json",
        )

        with pytest.raises(GitHubError) as caught:
            client_for(site).repository(REF)

        assert "rate limit" in str(caught.value).lower()


class TestResolveCommit:
    def test_the_default_branch_is_pinned_to_a_commit_sha(self, site: StubSite) -> None:
        assert client_for(site).resolve_commit(REF) == COMMIT

    def test_an_explicit_revision_may_be_pinned_instead(self, site: StubSite) -> None:
        site.add(
            api("/repos/octo/gateway/commits/v2.1.0"),
            json.dumps({"sha": "b" * 40}),
            content_type="application/json",
        )

        assert client_for(site).resolve_commit(REF, "v2.1.0") == "b" * 40

    def test_a_revision_that_would_escape_the_api_path_is_refused(self, site: StubSite) -> None:
        with pytest.raises(GitHubError):
            client_for(site).resolve_commit(REF, "../../../users/octo")

    def test_an_answer_that_is_not_a_commit_sha_is_refused(self, site: StubSite) -> None:
        site.add(
            api("/repos/octo/gateway/commits/main"),
            json.dumps({"sha": "main"}),
            content_type="application/json",
        )

        with pytest.raises(GitHubError):
            client_for(site).resolve_commit(REF)


class TestTree:
    def test_a_complete_recursive_tree_is_used_as_is(self, site: StubSite) -> None:
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
            json.dumps(
                {
                    "sha": ROOT_TREE,
                    "truncated": False,
                    "tree": [
                        entry("README.md"),
                        entry("src", kind="tree", mode="040000", size=None),
                        entry("src/app.py"),
                    ],
                }
            ),
            content_type="application/json",
        )

        listing = collect_files(client_for(site), REF, COMMIT)

        assert [item.path for item in listing.files] == ["README.md", "src/app.py"]
        assert listing.truncated is False
        assert listing.tree_requests == 1

    def test_a_truncated_recursive_tree_is_walked_subtree_by_subtree(
        self, site: StubSite
    ) -> None:
        """The whole point: a partial answer must never be mistaken for the repository."""
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
            json.dumps({"sha": ROOT_TREE, "truncated": True, "tree": [entry("README.md")]}),
            content_type="application/json",
        )
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}"),
            json.dumps(
                {
                    "sha": ROOT_TREE,
                    "truncated": False,
                    "tree": [
                        entry("README.md"),
                        entry("src", kind="tree", sha=SRC_TREE, mode="040000", size=None),
                    ],
                }
            ),
            content_type="application/json",
        )
        site.add(
            api(f"/repos/octo/gateway/git/trees/{SRC_TREE}"),
            json.dumps(
                {
                    "sha": SRC_TREE,
                    "truncated": False,
                    "tree": [
                        entry("app.py"),
                        entry("utils", kind="tree", sha=UTILS_TREE, mode="040000", size=None),
                    ],
                }
            ),
            content_type="application/json",
        )
        site.add(
            api(f"/repos/octo/gateway/git/trees/{UTILS_TREE}"),
            json.dumps({"sha": UTILS_TREE, "truncated": False, "tree": [entry("helpers.py")]}),
            content_type="application/json",
        )

        listing = collect_files(client_for(site), REF, COMMIT)

        assert [item.path for item in listing.files] == [
            "README.md",
            "src/app.py",
            "src/utils/helpers.py",
        ]
        assert listing.recovered_from_truncation is True

    def test_the_number_of_tree_requests_is_bounded(self, site: StubSite) -> None:
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
            json.dumps({"sha": ROOT_TREE, "truncated": True, "tree": []}),
            content_type="application/json",
        )
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}"),
            json.dumps(
                {
                    "sha": ROOT_TREE,
                    "truncated": False,
                    "tree": [
                        entry(f"dir{index}", kind="tree", sha=f"{index}".rjust(40, "0"),
                              mode="040000", size=None)
                        for index in range(50)
                    ],
                }
            ),
            content_type="application/json",
        )
        for index in range(50):
            site.add(
                api(f"/repos/octo/gateway/git/trees/{str(index).rjust(40, '0')}"),
                json.dumps({"sha": "x", "truncated": False, "tree": [entry("file.py")]}),
                content_type="application/json",
            )

        listing = collect_files(client_for(site), REF, COMMIT, limits=TreeLimits(max_requests=5))

        assert listing.truncated is True
        assert listing.tree_requests <= 5

    def test_a_subtree_that_is_itself_truncated_is_reported(self, site: StubSite) -> None:
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
            json.dumps({"sha": ROOT_TREE, "truncated": True, "tree": []}),
            content_type="application/json",
        )
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}"),
            json.dumps({"sha": ROOT_TREE, "truncated": True, "tree": [entry("only.py")]}),
            content_type="application/json",
        )

        listing = collect_files(client_for(site), REF, COMMIT)

        assert listing.truncated is True
        assert [item.path for item in listing.files] == ["only.py"]

    def test_submodules_and_symlinks_are_recorded_rather_than_followed(
        self, site: StubSite
    ) -> None:
        site.add(
            api(f"/repos/octo/gateway/git/trees/{COMMIT}?recursive=1"),
            json.dumps(
                {
                    "sha": ROOT_TREE,
                    "truncated": False,
                    "tree": [
                        entry("src/app.py"),
                        entry("vendored", kind="commit", mode="160000", size=None),
                        entry("link.py", mode="120000"),
                    ],
                }
            ),
            content_type="application/json",
        )

        listing = collect_files(client_for(site), REF, COMMIT)

        assert [item.path for item in listing.files] == ["src/app.py"]
        assert "vendored" in listing.submodules
        assert "link.py" in listing.symlinks

    def test_a_tree_the_api_will_not_serve_raises(self, site: StubSite) -> None:
        with pytest.raises(GitHubError):
            collect_files(client_for(site), REF, COMMIT)

    def test_truncation_that_cannot_be_recovered_is_visible_to_the_caller(self) -> None:
        assert issubclass(TruncatedTree, GitHubError)


class TestBlob:
    def test_a_blob_is_decoded_from_base64(self, site: StubSite) -> None:
        payload = b"def build_gateway():\n    return Gateway()\n"
        site.add(
            api(f"/repos/octo/gateway/git/blobs/{'a' * 40}"),
            json.dumps(
                {
                    "sha": "a" * 40,
                    "size": len(payload),
                    "encoding": "base64",
                    "content": base64.b64encode(payload).decode(),
                }
            ),
            content_type="application/json",
        )

        assert client_for(site).blob(REF, "a" * 40) == payload

    def test_an_unexpected_encoding_is_refused(self, site: StubSite) -> None:
        site.add(
            api(f"/repos/octo/gateway/git/blobs/{'a' * 40}"),
            json.dumps({"sha": "a" * 40, "size": 3, "encoding": "utf-8", "content": "abc"}),
            content_type="application/json",
        )

        with pytest.raises(GitHubError):
            client_for(site).blob(REF, "a" * 40)

    def test_a_blob_sha_that_is_not_a_sha_never_becomes_a_request(
        self, site: StubSite
    ) -> None:
        with pytest.raises(GitHubError):
            client_for(site).blob(REF, "../../../../etc/passwd")

        assert not any("etc/passwd" in record.target for record in site.requests)
