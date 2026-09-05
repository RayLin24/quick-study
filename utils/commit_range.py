"""Commit-range reading guide, more general than a PR (feature 12)."""

from __future__ import annotations

import subprocess
from pathlib import Path


def _run_git(cwd: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ValueError((result.stderr or result.stdout or "git failed").strip() or "git failed")
    return result.stdout


def commit_range_guide(repo_dir: str | Path, since: str, until: str = "HEAD") -> dict:
    root = Path(repo_dir)
    if not (root / ".git").exists() and not (root / ".git").is_file():
        raise ValueError("目录不是 git 仓库")
    since = (since or "").strip()
    until = (until or "HEAD").strip() or "HEAD"
    if not since:
        raise ValueError("请指定起始 commit")
    spec = f"{since}..{until}"
    log = _run_git(root, ["log", "--oneline", spec])
    names = _run_git(root, ["diff", "--name-only", spec])
    commits = [line.strip() for line in log.splitlines() if line.strip()]
    files = [line.strip() for line in names.splitlines() if line.strip()]
    lines = [
        f"# Commit range {spec}",
        "",
        f"- commits: {len(commits)}",
        f"- files: {len(files)}",
        "",
        "## 建议导读顺序",
        "",
    ]
    for path in files[:20]:
        lines.append(f"- `{path}`")
    if commits:
        lines.extend(["", "## Commits", ""])
        lines.extend(f"- {item}" for item in commits[:20])
    return {
        "since": since,
        "until": until,
        "spec": spec,
        "commits": commits,
        "files": files,
        "markdown": "\n".join(lines) + "\n",
    }
