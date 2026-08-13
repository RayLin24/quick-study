"""Deciding what repository a user meant, strictly.

Every API path, every citation prefix and every provenance record downstream is built from
the two strings this module returns, so a permissive parser here becomes a request-forgery
primitive: ``octo/gateway/../../users/octo`` would address a different endpoint entirely.

Only github.com is accepted, only in shapes that address a repository and nothing deeper,
and only with names GitHub itself would issue.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

GITHUB_HOSTS: Final[frozenset[str]] = frozenset({"github.com", "www.github.com"})

#: GitHub accounts: alphanumeric and hyphens, no leading or trailing hyphen, 39 maximum.
_OWNER: Final = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")

#: Repository names: alphanumerics with dots, hyphens and underscores, 100 maximum.
_NAME: Final = re.compile(r"\A[A-Za-z0-9_.-]{1,100}\Z")

_SCP_SYNTAX: Final = re.compile(r"\Agit@(?P<host>[A-Za-z0-9.-]+):(?P<path>.+)\Z")
_GIT_SUFFIX: Final = ".git"


class RepositoryRefError(ValueError):
    """Raised when input does not unambiguously name one public GitHub repository."""


@dataclass(frozen=True, slots=True)
class RepositoryRef:
    """One repository, as ``owner`` and ``name``."""

    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def html_url(self) -> str:
        return f"https://github.com/{self.full_name}"

    def __str__(self) -> str:
        return self.full_name


def parse_repository(spec: str) -> RepositoryRef:
    """Return the repository ``spec`` names, refusing anything ambiguous."""
    if not isinstance(spec, str):
        raise RepositoryRefError(f"a repository is named by a string, got {type(spec).__name__}")
    candidate = spec.strip()
    if not candidate:
        raise RepositoryRefError("no repository given")
    if "\\" in candidate or any(character in candidate for character in "\x00 \t\n\r"):
        raise RepositoryRefError(f"{spec!r} contains characters a repository name cannot")

    owner, name = _split(candidate)
    if not _OWNER.match(owner):
        raise RepositoryRefError(f"{owner!r} is not a GitHub account name")
    name = name[: -len(_GIT_SUFFIX)] if name.endswith(_GIT_SUFFIX) else name
    if not _NAME.match(name) or name in (".", ".."):
        raise RepositoryRefError(f"{name!r} is not a GitHub repository name")
    return RepositoryRef(owner=owner, name=name)


def _split(candidate: str) -> tuple[str, str]:
    """Reduce every accepted spelling to exactly two path segments."""
    scp = _SCP_SYNTAX.match(candidate)
    if scp is not None:
        _require_github(scp.group("host"), candidate)
        return _two_segments(scp.group("path"), candidate)

    if "://" in candidate:
        parts = urlsplit(candidate)
        if parts.scheme not in ("http", "https"):
            raise RepositoryRefError(f"{parts.scheme!r} is not an accepted scheme")
        if parts.username or parts.password:
            raise RepositoryRefError("credentials in a repository URL are not accepted")
        if parts.query or parts.fragment:
            raise RepositoryRefError("a repository URL carries no query or fragment")
        _require_github((parts.hostname or "").lower(), candidate)
        return _two_segments(parts.path, candidate)

    if candidate.startswith("/"):
        raise RepositoryRefError(f"{candidate!r} is a path, not a repository")
    head, _, tail = candidate.partition("/")
    if head.lower() in GITHUB_HOSTS:
        return _two_segments(tail, candidate)
    return _two_segments(candidate, candidate)


def _require_github(host: str, candidate: str) -> None:
    if host.lower() not in GITHUB_HOSTS:
        raise RepositoryRefError(f"{candidate!r} is not a github.com repository")


def _two_segments(path: str, candidate: str) -> tuple[str, str]:
    """Require exactly ``owner/name``, with no empty segment hiding a second meaning."""
    segments = path.strip("/").split("/")
    if len(segments) != 2 or not all(segments):
        raise RepositoryRefError(f"{candidate!r} does not name exactly one repository")
    return segments[0], segments[1]
