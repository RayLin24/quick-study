"""Post-push docs-drift adapter (#31): stale → branch → regen affected chapters (MVP)."""

from __future__ import annotations

import re
from pathlib import Path

from utils.ask_tutorial import SOURCE_RE
from utils.push_hook import incremental_job_from_push
from utils.stale import stale_status

SAFE_BRANCH = re.compile(r"[^A-Za-z0-9._/-]+")


def changed_paths_from_push(event: dict) -> list[str]:
    paths: list[str] = []
    for commit in (event or {}).get("commits") or []:
        for key in ("added", "modified", "removed"):
            for item in commit.get(key) or []:
                if item and item not in paths:
                    paths.append(str(item))
    head = (event or {}).get("head_commit") or {}
    for key in ("added", "modified", "removed"):
        for item in head.get(key) or []:
            if item and item not in paths:
                paths.append(str(item))
    return paths


def chapters_for_paths(folder: Path, changed: list[str]) -> list[dict]:
    needles = {str(p).replace("\\", "/").lstrip("./") for p in changed if p}
    hits = []
    for path in sorted(Path(folder).glob("*.md")):
        if path.name in {"index.md", "README.md", "glossary.md", "heatmap.md"}:
            continue
        text = path.read_text(encoding="utf-8")
        sources = [s.replace("\\", "/").lstrip("./") for s in SOURCE_RE.findall(text)]
        matched = [src for src in sources if src in needles or any(src.endswith(n) or n.endswith(src) for n in needles)]
        if matched:
            hits.append({"filename": path.name, "sources": matched})
    return hits


def drift_branch_name(repo_name: str | None, sha: str | None) -> str:
    short = (sha or "head")[:8]
    base = SAFE_BRANCH.sub("-", (repo_name or "docs").strip()) or "docs"
    return f"docs/drift-{base}-{short}"


def plan_docs_drift(
    folder: Path | None,
    event: dict | None = None,
    *,
    changed: list[str] | None = None,
) -> dict:
    """MVP: detect stale, name a branch, list affected chapters, return regen payload."""
    event = event or {}
    repo = (event.get("repository") or {}) if event else {}
    name = (folder.name if folder else None) or repo.get("name") or "tutorial"
    files = list(changed) if changed is not None else changed_paths_from_push(event)
    stale = stale_status(folder) if folder and Path(folder).is_dir() else {"stale": False, "reason": "no_folder"}
    affected = chapters_for_paths(folder, files) if folder and files else []
    sha = (event.get("after") or event.get("head_commit", {}).get("id") or stale.get("current") or "")[:40]
    branch = drift_branch_name(name, sha)
    job = incremental_job_from_push(event) if event.get("repository") else {
        "source_type": "dir" if folder else "repo",
        "name": name,
        "incremental": True,
        "resume": True,
    }
    if affected:
        job["retry_chapter"] = affected[0]["filename"]
        job["include"] = " ".join(sorted({src for item in affected for src in item["sources"]}))
    return {
        "stale": bool(stale.get("stale")) or bool(affected) or bool(files),
        "reason": "files_changed" if files else stale.get("reason"),
        "branch": branch,
        "changed": files,
        "chapters": affected,
        "job": job,
        "mvp": True,
        "note": "MVP：检测过期/受影响章并给出增量再生成载荷；真正开 PR 需宿主 git/GH 权限。",
    }
