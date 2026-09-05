"""Mark a tutorial stale when upstream commit drifted (feature 36)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


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
    stale = bool(recorded and current and recorded != current)
    return {
        "stale": stale,
        "recorded": recorded or None,
        "current": current,
        "reason": "commit_drift" if stale else ("no_commit" if not recorded else "in_sync"),
    }
