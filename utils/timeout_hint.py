from __future__ import annotations

import os


def job_timeout_hint(chapter_count: int, *, per_chapter: float | None = None) -> dict:
    each = per_chapter if per_chapter is not None else float(os.getenv("LLM_TIMEOUT_SECONDS") or 300)
    chapters = max(1, int(chapter_count or 1))
    suggested = max(each, chapters * each * 0.35 + each)
    return {
        "chapters": chapters,
        "per_chapter_seconds": each,
        "suggested_job_timeout": int(suggested),
        "hint": f"{chapters} 章 × 约 {int(each)}s，建议任务超时 ≥ {int(suggested)}s",
    }
