"""Enumerating a repository's files, including when the API will not do it in one go.

GitHub's recursive tree endpoint answers large repositories with a *partial* list and
``"truncated": true``. A client that ignores that flag analyses whatever fraction arrived
and reports success, so this module treats truncation as a first-class outcome: it falls
back to walking one tree object at a time, and if even that runs out of budget it says so
rather than pretending the listing is complete.

Submodules and symlinks are recorded and never followed. A submodule points at another
repository and a symlink points wherever its author chose; neither is this repository's
content.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Final

from app.ingestion.github.client import GitHubClient, TreeEntry
from app.ingestion.github.refs import RepositoryRef

#: Enough to walk a large repository, small enough to bound a hostile one.
DEFAULT_MAX_TREE_REQUESTS: Final = 400
DEFAULT_MAX_FILES: Final = 5000
DEFAULT_MAX_DEPTH: Final = 24


@dataclass(frozen=True, slots=True)
class TreeLimits:
    max_requests: int = DEFAULT_MAX_TREE_REQUESTS
    max_files: int = DEFAULT_MAX_FILES
    max_depth: int = DEFAULT_MAX_DEPTH


@dataclass(frozen=True, slots=True)
class RepositoryListing:
    """Every file at one commit, and an honest account of what was left out."""

    commit_sha: str
    files: tuple[TreeEntry, ...] = ()
    submodules: tuple[str, ...] = ()
    symlinks: tuple[str, ...] = ()
    #: True when the listing is known to be incomplete, whatever the reason.
    truncated: bool = False
    #: True when a truncated recursive answer was successfully replaced by a full walk.
    recovered_from_truncation: bool = False
    tree_requests: int = 0


def collect_files(
    client: GitHubClient,
    ref: RepositoryRef,
    commit_sha: str,
    *,
    limits: TreeLimits | None = None,
) -> RepositoryListing:
    """List every blob reachable from ``commit_sha``."""
    budget = limits or TreeLimits()
    recursive = client.tree(ref, commit_sha, recursive=True)
    if not recursive.truncated:
        collector = _Collector(budget)
        collector.absorb(recursive.entries, prefix="")
        return collector.finish(commit_sha, requests=1, recovered=False)
    return _walk(client, ref, commit_sha, budget)


def _walk(
    client: GitHubClient,
    ref: RepositoryRef,
    commit_sha: str,
    budget: TreeLimits,
) -> RepositoryListing:
    """Enumerate tree by tree, which the API never truncates for a single directory."""
    collector = _Collector(budget)
    pending: deque[tuple[str, str, int]] = deque([(commit_sha, "", 0)])
    requests = 1  # the recursive attempt that came back truncated
    exhausted = False

    while pending:
        if requests >= budget.max_requests:
            exhausted = True
            break
        sha, prefix, depth = pending.popleft()
        tree = client.tree(ref, sha, recursive=False)
        requests += 1
        if tree.truncated:
            collector.truncated = True
        for child in collector.absorb(tree.entries, prefix=prefix):
            if depth + 1 <= budget.max_depth:
                pending.append((child.sha, f"{prefix}{child.path}/", depth + 1))
            else:
                collector.truncated = True

    if exhausted:
        collector.truncated = True
    return collector.finish(
        commit_sha, requests=requests, recovered=not collector.truncated
    )


class _Collector:
    """Accumulates one listing, keeping paths absolute from the repository root."""

    def __init__(self, budget: TreeLimits) -> None:
        self._budget = budget
        self.files: list[TreeEntry] = []
        self.submodules: list[str] = []
        self.symlinks: list[str] = []
        self.truncated = False

    def absorb(self, entries: tuple[TreeEntry, ...], *, prefix: str) -> list[TreeEntry]:
        """Record ``entries`` and return the subtrees that still need visiting."""
        subtrees: list[TreeEntry] = []
        for entry in entries:
            path = f"{prefix}{entry.path}"
            if entry.is_submodule:
                self.submodules.append(path)
            elif entry.is_symlink:
                self.symlinks.append(path)
            elif entry.is_tree:
                subtrees.append(entry)
            elif entry.is_blob:
                if len(self.files) >= self._budget.max_files:
                    self.truncated = True
                    continue
                self.files.append(_at(entry, path))
        return subtrees

    def finish(self, commit_sha: str, *, requests: int, recovered: bool) -> RepositoryListing:
        return RepositoryListing(
            commit_sha=commit_sha,
            files=tuple(self.files),
            submodules=tuple(self.submodules),
            symlinks=tuple(self.symlinks),
            truncated=self.truncated,
            recovered_from_truncation=recovered and not self.truncated,
            tree_requests=requests,
        )


def _at(entry: TreeEntry, path: str) -> TreeEntry:
    return entry if entry.path == path else TreeEntry(
        path=path, mode=entry.mode, type=entry.type, sha=entry.sha, size=entry.size
    )
