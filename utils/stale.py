"""Mark a tutorial stale when upstream commit drifted (feature 36).

Public-repo tutorials usually have ``repo_url`` and no local ``.git``.
``stale_status`` therefore compares ``meta.upstream_commit`` against the
GitHub / GitLab / Gitea API HEAD, not only ``git rev-parse``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_REMOTE_TIMEOUT = float(os.getenv("STALE_REMOTE_TIMEOUT", "12"))


def git_head(repo_dir: str | Path) -> str | None:
    root = Path(repo_dir)
    if not (root / ".git").exists() and not (root / ".git").is_file():
        return None
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def save_upstream_commit(folder: Path, sha: str) -> None:
    meta_path = Path(folder) / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    meta["upstream_commit"] = sha
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def _auth_headers(token: str | None = None) -> dict[str, str]:
    headers = {"Accept": "application/json"}
    secret = (token or os.getenv("GITHUB_TOKEN") or os.getenv("GITLAB_TOKEN") or "").strip()
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    return headers


def _github_ref(remainder: str | None, default_branch: str | None) -> str:
    text = (remainder or "").strip("/")
    if not text:
        return default_branch or "HEAD"
    parts = text.split("/")
    # Prefer a non-SHA first segment as a branch name; full remainder next
    # (release/1.0). A 40-hex pin still falls back to the default branch so
    # stale compares "what we generated" vs current tip.
    first = parts[0]
    if SHA_RE.fullmatch(first) and len(first) >= 40:
        return default_branch or first
    if default_branch and (text == default_branch or text.startswith(default_branch + "/")):
        return default_branch
    return first


def remote_head(repo_url: str | None, *, token: str | None = None, timeout: float | None = None) -> str | None:
    """Return the remote tip SHA for a public GitHub / GitLab / Gitea URL."""
    url = (repo_url or "").strip()
    if not url:
        return None
    timeout = _REMOTE_TIMEOUT if timeout is None else timeout
    headers = _auth_headers(token)
    try:
        from utils.gitlab_gitea import classify_repo_url

        kind = classify_repo_url(url)
    except Exception:
        kind = None
    if kind is None and "github.com" in url:
        kind = "github"
    try:
        if kind == "github":
            return _github_remote_head(url, headers=headers, timeout=timeout)
        if kind == "gitlab":
            return _gitlab_remote_head(url, headers=headers, timeout=timeout)
        if kind == "gitea":
            return _gitea_remote_head(url, headers=headers, timeout=timeout)
    except (requests.RequestException, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return None
    return None


def _github_remote_head(url: str, *, headers: dict, timeout: float) -> str | None:
    from utils.crawl_github_files import parse_github_http_url

    owner, repo, remainder = parse_github_http_url(url)
    info = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}",
        headers={**headers, "Accept": "application/vnd.github+json"},
        timeout=timeout,
    )
    default_branch = None
    if info.status_code == 200:
        default_branch = (info.json() or {}).get("default_branch")
    ref = _github_ref(remainder, default_branch)
    commit = requests.get(
        f"https://api.github.com/repos/{owner}/{repo}/commits/{quote(str(ref))}",
        headers={**headers, "Accept": "application/vnd.github+json"},
        timeout=timeout,
    )
    if commit.status_code != 200:
        return None
    sha = (commit.json() or {}).get("sha")
    return str(sha) if sha else None


def _gitlab_remote_head(url: str, *, headers: dict, timeout: float) -> str | None:
    parsed = urlparse(url)
    parts = [p for p in (parsed.path or "").split("/") if p and p != "-"]
    if len(parts) < 2:
        return None
    project = quote("/".join(parts), safe="")
    info = requests.get(
        f"https://gitlab.com/api/v4/projects/{project}",
        headers=headers,
        timeout=timeout,
    )
    default_branch = "main"
    if info.status_code == 200:
        default_branch = (info.json() or {}).get("default_branch") or default_branch
    commit = requests.get(
        f"https://gitlab.com/api/v4/projects/{project}/repository/commits/{quote(default_branch)}",
        headers=headers,
        timeout=timeout,
    )
    if commit.status_code != 200:
        return None
    sha = (commit.json() or {}).get("id")
    return str(sha) if sha else None


def _gitea_remote_head(url: str, *, headers: dict, timeout: float) -> str | None:
    parsed = urlparse(url)
    parts = [p for p in (parsed.path or "").split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    host = parsed.hostname or "gitea.com"
    info = requests.get(
        f"https://{host}/api/v1/repos/{owner}/{repo}",
        headers=headers,
        timeout=timeout,
    )
    branch = "main"
    if info.status_code == 200:
        branch = (info.json() or {}).get("default_branch") or branch
    commits = requests.get(
        f"https://{host}/api/v1/repos/{owner}/{repo}/commits",
        headers=headers,
        params={"sha": branch, "limit": 1},
        timeout=timeout,
    )
    if commits.status_code != 200:
        return None
    payload = commits.json()
    if isinstance(payload, list) and payload:
        sha = payload[0].get("sha")
        return str(sha) if sha else None
    return None


def resolve_upstream_commit(
    *,
    repo_url: str | None = None,
    local_dir: str | Path | None = None,
    token: str | None = None,
) -> str | None:
    if local_dir:
        sha = git_head(local_dir)
        if sha:
            return sha
    if repo_url:
        return remote_head(repo_url, token=token)
    return None


def stale_status(folder: Path, *, local_dir: str | Path | None = None) -> dict:
    meta_path = Path(folder) / "meta.json"
    meta = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    recorded = meta.get("upstream_commit") or ""
    target = local_dir or meta.get("local_dir")
    current = git_head(target) if target else None
    source = "local" if current else None
    if not current and meta.get("repo_url"):
        current = remote_head(meta.get("repo_url"))
        source = "remote" if current else None
    stale = bool(recorded and current and recorded != current)
    if stale:
        reason = "commit_drift"
    elif not recorded:
        reason = "no_commit"
    elif not current:
        reason = "unresolved"
    else:
        reason = "in_sync"
    return {
        "stale": stale,
        "recorded": recorded or None,
        "current": current,
        "reason": reason,
        "source": source,
        "repo_url": meta.get("repo_url"),
    }
