from __future__ import annotations

import os

from web.bind import token_from_headers

WRITE_PREFIXES = (
    "/api/jobs",
    "/v1/jobs",
    "/api/hooks",
    "/api/workbench",
    "/api/ops",
)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ALWAYS_OPEN = {"/login", "/healthz", "/api/config"}


def read_token() -> str:
    return (os.getenv("QUICK_STUDY_READ_TOKEN") or "").strip()


def write_token() -> str:
    return (os.getenv("QUICK_STUDY_TOKEN") or "").strip()


def is_write_path(method: str, path: str) -> bool:
    if method.upper() not in WRITE_METHODS:
        return False
    if path.startswith("/api/tutorials/") and path.rstrip("/").endswith("/ask"):
        return False
    if path.startswith("/api/tutorials/") and path.rstrip("/").endswith("/ask/events"):
        return False
    if path.startswith("/v1/tutorials/") and path.rstrip("/").endswith("/ask"):
        return False
    if path.startswith("/v1/tutorials/") and path.rstrip("/").endswith("/ask/events"):
        return False
    if path.startswith("/api/tutorials/") and "/annotations" in path:
        return False
    if path.startswith("/api/tutorials/") and path.rstrip("/").endswith("/star"):
        return False
    if any(path == prefix or path.startswith(prefix + "/") for prefix in WRITE_PREFIXES):
        return True
    if path.startswith("/api/tutorials/") and method.upper() in {"DELETE", "POST", "PUT", "PATCH"}:
        return True
    return False


def classify_token(headers, cookies=None) -> str:
    """Return 'write', 'read', or ''."""
    got = token_from_headers(headers, cookies)
    if not got:
        return ""
    if write_token() and got == write_token():
        return "write"
    if read_token() and got == read_token():
        return "read"
    return ""
