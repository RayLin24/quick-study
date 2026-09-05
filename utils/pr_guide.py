from __future__ import annotations

import re

DIFF_HUNK = re.compile(r"^@@.*@@", re.M)
FILE_HEAD = re.compile(r"^\+\+\+ b/(.+)$", re.M)


def parse_pr_diff(diff_text: str, *, max_files: int = 40) -> dict:
    files = FILE_HEAD.findall(diff_text or "")
    hunks = DIFF_HUNK.findall(diff_text or "")
    return {
        "files": files[:max_files],
        "file_count": len(files),
        "hunk_count": len(hunks),
        "chars": len(diff_text or ""),
    }


def build_pr_guide_prompt(diff_text: str, *, title: str = "PR") -> str:
    info = parse_pr_diff(diff_text)
    listing = "\n".join(f"- {path}" for path in info["files"]) or "- (no files)"
    excerpt = (diff_text or "")[:6000]
    return (
        f"Write a beginner-friendly reading guide for this pull request: {title}.\n"
        f"Changed files ({info['file_count']}):\n{listing}\n\n"
        f"Diff excerpt:\n{excerpt}\n\n"
        "Explain what changed, the safe reading order, and what to ignore."
    )
