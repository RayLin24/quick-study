from __future__ import annotations

import re

GITHUB_OWNER_REPO = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def deepwiki_url(repo_url: str | None) -> str | None:
    if not repo_url:
        return None
    match = GITHUB_OWNER_REPO.match(repo_url.strip())
    if not match:
        return None
    owner, repo = match.group("owner"), match.group("repo").removesuffix(".git")
    return f"https://deepwiki.com/{owner}/{repo}"
