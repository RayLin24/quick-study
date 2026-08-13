"""A small, deliberately incomplete GitHub API client.

It knows four things: what a repository is, which commit a revision points at, what a tree
contains and what a blob holds. That is everything needed to read a repository at a fixed
revision, and adding more would only widen what a compromised or hostile response could
reach.

Requests go through the same guarded fetcher as everything else, so the API host is
resolved and screened like any other and redirects are not followed automatically. Every
value interpolated into a path is validated first: a SHA is forty hex characters or it is
not a request.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any, Final

from app.ingestion.github.refs import RepositoryRef
from app.ingestion.web.fetcher import FetchedResponse, SafeFetcher
from app.ingestion.web.urls import normalize_url

GITHUB_API_BASE: Final = "https://api.github.com"

#: Pinned so a future default does not silently change the response shape.
GITHUB_API_VERSION: Final = "2022-11-28"

GITHUB_MEDIA_TYPE: Final = "application/vnd.github+json"

_SHA: Final = re.compile(r"\A[0-9a-f]{40}\Z")

#: Branches, tags and short SHAs. Deliberately excludes ``/``, ``.`` runs and ``..``.
_REVISION: Final = re.compile(r"\A[A-Za-z0-9._-]{1,255}\Z")


class GitHubError(Exception):
    """Base class for a repository that could not be read."""


class RepositoryNotFound(GitHubError):
    """Raised when the API says the repository does not exist, or is not visible."""


class RepositoryNotPublic(GitHubError):
    """Raised for a private repository. The product boundary is public sources only."""


class TruncatedTree(GitHubError):
    """Raised when a tree could not be enumerated completely within its budget."""


@dataclass(frozen=True, slots=True)
class RepositoryInfo:
    full_name: str
    default_branch: str
    private: bool = False
    fork: bool = False
    archived: bool = False
    size_kb: int = 0
    html_url: str = ""


@dataclass(frozen=True, slots=True)
class TreeEntry:
    """One entry of a git tree, exactly as the API describes it."""

    path: str
    mode: str
    type: str
    sha: str
    size: int | None = None

    @property
    def is_blob(self) -> bool:
        return self.type == "blob"

    @property
    def is_tree(self) -> bool:
        return self.type == "tree"

    @property
    def is_submodule(self) -> bool:
        return self.type == "commit" or self.mode == "160000"

    @property
    def is_symlink(self) -> bool:
        return self.mode == "120000"


@dataclass(frozen=True, slots=True)
class Tree:
    sha: str
    entries: tuple[TreeEntry, ...]
    truncated: bool = False


class GitHubClient:
    """Reads one repository at a time. Holds no state beyond its configuration."""

    def __init__(
        self,
        fetcher: SafeFetcher,
        *,
        token: str | None = None,
        api_base: str = GITHUB_API_BASE,
    ) -> None:
        self._fetcher = fetcher
        self._token = token
        self._api_base = api_base.rstrip("/")

    def repository(self, ref: RepositoryRef) -> RepositoryInfo:
        """Read the repository's metadata, refusing anything that is not public."""
        payload = self._get(f"/repos/{ref.owner}/{ref.name}")
        info = RepositoryInfo(
            full_name=str(payload.get("full_name") or ref.full_name),
            default_branch=str(payload.get("default_branch") or "main"),
            private=bool(payload.get("private", False)),
            fork=bool(payload.get("fork", False)),
            archived=bool(payload.get("archived", False)),
            size_kb=int(payload.get("size") or 0),
            html_url=str(payload.get("html_url") or ref.html_url),
        )
        if info.private:
            raise RepositoryNotPublic(f"{ref} is private; only public sources are supported")
        return info

    def resolve_commit(self, ref: RepositoryRef, revision: str | None = None) -> str:
        """Pin a revision to a commit SHA.

        A branch moves. Everything after this point — the tree, the blobs, the citations —
        is addressed by the SHA returned here, so a repository read twice a week apart is
        either identical or visibly a different snapshot.
        """
        target = revision or self.repository(ref).default_branch
        if not _REVISION.match(target) or ".." in target:
            raise GitHubError(f"{target!r} is not a usable revision")
        payload = self._get(f"/repos/{ref.owner}/{ref.name}/commits/{target}")
        sha = str(payload.get("sha", ""))
        if not _SHA.match(sha):
            raise GitHubError(f"{ref} resolved {target!r} to {sha!r}, which is not a commit")
        return sha

    def tree(self, ref: RepositoryRef, sha: str, *, recursive: bool = False) -> Tree:
        """Read one tree object, optionally asking the API to expand it."""
        self._require_sha(sha)
        suffix = "?recursive=1" if recursive else ""
        payload = self._get(f"/repos/{ref.owner}/{ref.name}/git/trees/{sha}{suffix}")
        entries = tuple(
            TreeEntry(
                path=str(item.get("path", "")),
                mode=str(item.get("mode", "")),
                type=str(item.get("type", "")),
                sha=str(item.get("sha", "")),
                size=item.get("size"),
            )
            for item in payload.get("tree", [])
            if isinstance(item, dict)
        )
        return Tree(
            sha=str(payload.get("sha") or sha),
            entries=entries,
            truncated=bool(payload.get("truncated", False)),
        )

    def blob(self, ref: RepositoryRef, sha: str) -> bytes:
        """Read one blob's bytes. The API delivers them base64-encoded."""
        self._require_sha(sha)
        payload = self._get(f"/repos/{ref.owner}/{ref.name}/git/blobs/{sha}")
        encoding = str(payload.get("encoding", ""))
        if encoding != "base64":
            raise GitHubError(f"blob {sha} arrived as {encoding!r}, which is not understood")
        try:
            return base64.b64decode(str(payload.get("content", "")), validate=False)
        except (binascii.Error, ValueError) as error:
            raise GitHubError(f"blob {sha} is not decodable: {error}") from error

    def _require_sha(self, sha: str) -> None:
        if not _SHA.match(sha):
            raise GitHubError(f"{sha!r} is not a git object id")

    def _get(self, path: str) -> dict[str, Any]:
        response = self._fetcher.fetch(
            normalize_url(f"{self._api_base}{path}"),
            headers=self._headers(),
        )
        _raise_for_status(response, path)
        try:
            payload = json.loads(response.content or b"{}")
        except json.JSONDecodeError as error:
            raise GitHubError(f"{path} returned malformed JSON: {error}") from error
        if not isinstance(payload, dict):
            raise GitHubError(f"{path} returned {type(payload).__name__}, expected an object")
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {
            "accept": GITHUB_MEDIA_TYPE,
            "x-github-api-version": GITHUB_API_VERSION,
        }
        if self._token:
            headers["authorization"] = f"Bearer {self._token}"
        return headers


def _raise_for_status(response: FetchedResponse, path: str) -> None:
    if response.ok:
        return
    if response.status_code == 404:
        raise RepositoryNotFound(f"{path} does not exist or is not public")
    if response.status_code in (403, 429) and _is_rate_limited(response):
        raise GitHubError(f"{path} refused: GitHub rate limit reached")
    raise GitHubError(f"{path} returned {response.status_code}")


def _is_rate_limited(response: FetchedResponse) -> bool:
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    return "rate limit" in response.text.lower()
