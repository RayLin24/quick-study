"""Use the repo Open Graph image as tutorial cover (feature 46)."""

from __future__ import annotations

import re

GITHUB_RE = re.compile(r"https?://github\.com/([^/]+)/([^/]+)")


def og_image_url(repo_url: str | None) -> str | None:
    if not repo_url:
        return None
    match = GITHUB_RE.search(repo_url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2).removesuffix(".git")
    return f"https://opengraph.githubassets.com/1/{owner}/{repo}"


def og_cover_html(repo_url: str | None, title: str = "") -> str:
    href = og_image_url(repo_url)
    if not href:
        return ""
    label = title or "repository cover"
    return f'<figure class="cover cover-og"><img src="{href}" alt="{label}" class="shot"></figure>'
