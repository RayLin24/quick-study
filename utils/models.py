from __future__ import annotations

import os

STRUCTURE_STAGES = frozenset({"identify", "relationships", "order"})
WRITE_STAGES = frozenset({"write", "ask", "polish", "quiz"})


def structure_model(default: str) -> str:
    return (os.getenv("LLM_STRUCTURE_MODEL") or "").strip() or default


def write_model(default: str) -> str:
    return (os.getenv("LLM_WRITE_MODEL") or "").strip() or default


def model_for_stage(stage: str | None, default: str) -> str:
    name = (stage or "").strip().lower()
    if name in STRUCTURE_STAGES:
        return structure_model(default)
    if name in WRITE_STAGES:
        return write_model(default)
    return default
