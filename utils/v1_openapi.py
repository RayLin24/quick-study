"""Stable /v1 OpenAPI surface (#27). Experimental routes stay under /api."""

from __future__ import annotations

STABLE = (
    ("GET", "/v1/tutorials", "List generated tutorials"),
    ("POST", "/v1/tutorials/{name}/ask", "Ask a tutorial (JSON, non-stream)"),
    ("POST", "/v1/tutorials/{name}/ask/events", "Ask streaming SSE (separate billing)"),
    ("POST", "/v1/jobs", "Start a generation job"),
    ("GET", "/v1/jobs/current", "Poll current job"),
    ("GET", "/v1/jobs/current/events", "Job log SSE (pipeline, not Ask)"),
    ("POST", "/v1/jobs/preview", "Dry-run preview (no write)"),
    ("GET", "/v1/tutorials/{name}/export.zip", "Download tutorial zip"),
    ("GET", "/v1/openapi.json", "This document"),
)

EXPERIMENTAL_NOTE = (
    "/api/* 为实验面：搜索、标签、导入、Obsidian/EPUB、审计、preset、hooks、MCP 等。"
    "客户端请只用 /v1 的 list / ask / jobs（及本表列出的 preview / export / SSE）。"
)


def openapi_spec() -> dict:
    paths = {}
    for method, path, summary in STABLE:
        paths.setdefault(path, {})[method.lower()] = {
            "summary": summary,
            "tags": ["v1-stable"],
            "responses": {"200": {"description": "OK"}},
        }
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Quick Study v1",
            "version": "1.0.0",
            "description": (
                "稳定面：list / ask / jobs。Ask SSE 与流水线 job SSE 分开。"
                + EXPERIMENTAL_NOTE
            ),
        },
        "paths": paths,
        "x-quick-study": {
            "stable": [f"{m} {p}" for m, p, _s in STABLE],
            "experimental": "/api/*",
            "default_model": "z-ai/glm-5.3-flash",
        },
    }
