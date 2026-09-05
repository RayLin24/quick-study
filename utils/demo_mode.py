"""Read-only demo mode with a fixed sample repo (feature 30)."""

from __future__ import annotations

import os

DEMO_REPO = "https://github.com/octocat/Hello-World"
WRITE_HINTS = ("/api/jobs", "/api/hooks", "/retry-chapter", "/api/tutorials")


def demo_enabled() -> bool:
    return (os.getenv("QUICK_STUDY_DEMO") or "").strip().lower() in {"1", "true", "yes"}


def is_generate_path(method: str, path: str) -> bool:
    if method.upper() not in {"POST", "PUT", "PATCH", "DELETE"}:
        return False
    if path.startswith("/api/jobs"):
        return True
    if path.startswith("/api/hooks"):
        return True
    if path.startswith("/api/tutorials/") and path.rstrip("/").endswith("/retry-chapter"):
        return True
    if path == "/api/jobs/upload":
        return True
    return False


def demo_payload() -> dict:
    return {
        "demo": True,
        "repo": DEMO_REPO,
        "generate_disabled": True,
        "detail": "演示模式：固定样例仓，禁止生成",
    }
