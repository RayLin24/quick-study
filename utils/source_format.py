from __future__ import annotations

import re
from urllib.parse import quote

SOURCE_LINE_RE = re.compile(
    r"(?:^\s*)(?:\*source:|#\s*source:|\*\s*source:)\s*`?([^\s*`]+)`?",
    re.IGNORECASE | re.M,
)
UNIFIED_SOURCE = "*source: {path}*"


def unified_source_line(path: str) -> str:
    return UNIFIED_SOURCE.format(path=path)


def normalize_source_markup(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        return unified_source_line(match.group(1).strip())

    return SOURCE_LINE_RE.sub(repl, text or "")


def github_blob_url(repo_url: str, path: str) -> str | None:
    if not repo_url:
        return None
    base = repo_url.rstrip("/")
    if base.endswith(".git"):
        base = base[:-4]
    # /tree/ref/subpath → blob/ref/subpath/file
    parts = base.split("/")
    if "tree" in parts:
        idx = parts.index("tree")
        owner_repo = "/".join(parts[:idx])
        rest = parts[idx + 1 :]
        if not rest:
            return f"{owner_repo}/blob/HEAD/{quote(path)}"
        # first segment(s) may be a branch; we cannot perfectly split; use HEAD + path only
        return f"{owner_repo}/blob/HEAD/{quote(path)}"
    return f"{base}/blob/HEAD/{quote(path)}"


def linkify_source_html(html: str, *, repo_url: str | None = None) -> str:
    def repl(match: re.Match[str]) -> str:
        path = match.group(1).strip()
        href = github_blob_url(repo_url, path) if repo_url else None
        if href:
            return f'*source: <a href="{href}" target="_blank" rel="noreferrer">{path}</a>*'
        return (
            f'*source: <code class="source-path" data-path="{path}">{path}</code>'
            f'<button type="button" class="copy-path" data-path="{path}">复制路径</button>*'
        )

    return SOURCE_LINE_RE.sub(repl, html or "")
