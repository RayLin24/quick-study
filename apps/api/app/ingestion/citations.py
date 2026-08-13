"""The two reference formats a generated tutorial is allowed to cite.

Both pin a revision, because a citation that does not is unverifiable: a page changes and
a branch moves, so "the docs say X" is only checkable against the instant the page was
read or the commit the file was read at.

    web   https://docs.example.test/guide#requirements@2026-03-04T10:30:00Z
    repo  octo/gateway@9f2c1b7…5678/src/app.py#L10-L24

The shapes mirror each other — locator, then revision, then the part being quoted — and
both parse unambiguously: a timestamp contains no ``@``, and a commit SHA is exactly forty
hex characters, so the two are told apart without a scheme prefix.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

#: Matches the whole repository form. ``path`` is greedy up to the final ``#L``, so a file
#: name containing ``#`` still parses.
_REPO_REFERENCE: Final = re.compile(
    r"\A(?P<repo>[A-Za-z0-9][\w.-]*/[\w.-]+)"
    r"@(?P<commit>[0-9a-f]{40})"
    r"/(?P<path>.+)"
    r"#L(?P<start>\d+)(?:-L(?P<end>\d+))?\Z"
)
_WEB_REFERENCE: Final = re.compile(
    r"\A(?P<url>https?://[^\s]+?)#(?P<chunk>[^\s#@]+)@(?P<instant>[0-9TZ:+\-.]+)\Z"
)
_COMMIT_SHA: Final = re.compile(r"\A[0-9a-f]{40}\Z")
_REPO_NAME: Final = re.compile(r"\A[A-Za-z0-9][\w.-]*/[\w.-]+\Z")
_CHUNK_ID: Final = re.compile(r"\A[^\s#@]+\Z")

_INSTANT_FORMAT: Final = "%Y-%m-%dT%H:%M:%SZ"


class CitationError(ValueError):
    """Raised when a reference would be unverifiable, or is not a reference at all."""


@dataclass(frozen=True, slots=True)
class WebCitation:
    """A quotation from a page, pinned to the chunk and the moment it was fetched."""

    url: str
    fetched_at: datetime
    chunk_id: str

    @property
    def reference(self) -> str:
        return format_web_citation(self.url, self.fetched_at, self.chunk_id)


@dataclass(frozen=True, slots=True)
class RepoCitation:
    """A quotation from a repository file, pinned to a commit and a line range."""

    repo: str
    commit: str
    path: str
    start_line: int
    end_line: int | None = None

    @property
    def reference(self) -> str:
        return format_repo_citation(
            self.repo, self.commit, self.path, self.start_line, self.end_line
        )


def format_web_citation(url: str, fetched_at: datetime, chunk_id: str) -> str:
    """Render a page reference, refusing anything that could not be checked later."""
    if not url.startswith(("http://", "https://")):
        raise CitationError(f"a web citation needs an http(s) URL, got {url!r}")
    if "#" in url or " " in url:
        raise CitationError(f"a citable URL carries no fragment or spaces: {url!r}")
    if not _CHUNK_ID.match(chunk_id):
        raise CitationError(f"{chunk_id!r} is not a usable chunk identifier")
    return f"{url}#{chunk_id}@{_format_instant(fetched_at)}"


def format_repo_citation(
    repo: str,
    commit: str,
    path: str,
    start_line: int,
    end_line: int | None = None,
) -> str:
    """Render a repository reference against a fixed commit."""
    if not _REPO_NAME.match(repo):
        raise CitationError(f"{repo!r} is not an owner/name repository")
    if not _COMMIT_SHA.match(commit):
        raise CitationError(f"{commit!r} is not a full commit SHA; a citation cannot move")
    _validate_path(path)
    _validate_lines(start_line, end_line)
    if end_line is None or end_line == start_line:
        return f"{repo}@{commit}/{path}#L{start_line}"
    return f"{repo}@{commit}/{path}#L{start_line}-L{end_line}"


def parse_citation(text: str) -> WebCitation | RepoCitation:
    """Recover a citation from generated Markdown so it can be verified."""
    candidate = text.strip()
    repo_match = _REPO_REFERENCE.match(candidate)
    if repo_match is not None:
        return _repo_from(repo_match)
    web_match = _WEB_REFERENCE.match(candidate)
    if web_match is not None:
        return _web_from(web_match)
    raise CitationError(f"{text!r} is not a citation")


def _repo_from(match: re.Match[str]) -> RepoCitation:
    path = match.group("path")
    _validate_path(path)
    start = int(match.group("start"))
    end = int(match.group("end")) if match.group("end") else None
    _validate_lines(start, end)
    return RepoCitation(
        repo=match.group("repo"),
        commit=match.group("commit"),
        path=path,
        start_line=start,
        end_line=end,
    )


def _web_from(match: re.Match[str]) -> WebCitation:
    raw = match.group("instant")
    try:
        instant = datetime.strptime(raw, _INSTANT_FORMAT).replace(tzinfo=UTC)
    except ValueError as error:
        raise CitationError(f"{raw!r} is not a UTC instant") from error
    return WebCitation(
        url=match.group("url"), fetched_at=instant, chunk_id=match.group("chunk")
    )


def _format_instant(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise CitationError("a citation instant must carry a timezone")
    return moment.astimezone(UTC).strftime(_INSTANT_FORMAT)


def _validate_path(path: str) -> None:
    if not path or path.startswith("/") or "\\" in path:
        raise CitationError(f"{path!r} is not a repository-relative path")
    if any(segment in ("..", "") for segment in path.split("/")):
        raise CitationError(f"{path!r} leaves the repository")


def _validate_lines(start_line: int, end_line: int | None) -> None:
    if start_line < 1:
        raise CitationError("line numbers start at 1")
    if end_line is not None and end_line < start_line:
        raise CitationError(f"line range {start_line}-{end_line} ends before it starts")
