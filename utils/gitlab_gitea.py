from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests

GITHUB_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?(?:/tree/[^?\s#]+)?/?$",
    re.IGNORECASE,
)
GITLAB_RE = re.compile(
    r"^https://gitlab\.com/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/?$",
    re.IGNORECASE,
)
GITEA_RE = re.compile(
    r"^https://gitea\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$",
    re.IGNORECASE,
)


def allowed_extra_hosts() -> set[str]:
    raw = os.getenv("ALLOW_GIT_HOSTS") or ""
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def classify_repo_url(url: str) -> str | None:
    text = (url or "").strip()
    if GITHUB_RE.match(text):
        return "github"
    if GITLAB_RE.match(text):
        return "gitlab"
    if GITEA_RE.match(text):
        return "gitea"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host in allowed_extra_hosts() and parsed.scheme == "https":
        from utils.clone_guard import CloneRefused, assert_safe_clone_url

        try:
            assert_safe_clone_url(text)
        except CloneRefused:
            return None
        return "gitea"
    return None


def tree_api_url(url: str) -> str | None:
    kind = classify_repo_url(url)
    parsed = urlparse(url)
    parts = [p for p in (parsed.path or "").split("/") if p and p != "-"]
    if kind == "gitlab" and len(parts) >= 2:
        project = "%2F".join(parts[:2] if "gitlab.com" in (parsed.hostname or "") else parts)
        if len(parts) >= 2:
            project = "%2F".join(parts)
        return f"https://gitlab.com/api/v4/projects/{project}/repository/tree?recursive=true&per_page=100"
    if kind in {"gitea", "github"} and len(parts) >= 2:
        owner, repo = parts[0], parts[1].removesuffix(".git")
        if kind == "github":
            return f"https://api.github.com/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        host = parsed.hostname
        return f"https://{host}/api/v1/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
    return None


def clone_http_repo(url: str, dest: str | Path, *, token: str | None = None) -> Path:
    from utils.clone_guard import assert_safe_clone_url

    assert_safe_clone_url(url)
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    clone_url = url
    if token and url.startswith("https://"):
        clone_url = url.replace("https://", f"https://oauth2:{token}@", 1)
    from git import Repo

    Repo.clone_from(clone_url, str(target), depth=1)
    return target


def fetch_http_tree(url: str, *, token: str | None = None, timeout: float = 20) -> list[str]:
    api = tree_api_url(url)
    if not api:
        raise ValueError("不支持的仓库 URL")
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    resp = requests.get(api, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    paths: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict) and item.get("path") and item.get("type") in {None, "blob", "file"}:
                paths.append(item["path"])
    elif isinstance(data, dict):
        for item in data.get("tree") or []:
            if item.get("type") == "blob" and item.get("path"):
                paths.append(item["path"])
    return paths
