from __future__ import annotations

import hashlib
import hmac
import os


def verify_github_signature(secret: str, payload: bytes, header: str) -> bool:
    if not secret or not header:
        return False
    digest = "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, header)


def incremental_job_from_push(event: dict) -> dict:
    repo = (event or {}).get("repository") or {}
    clone = repo.get("clone_url") or repo.get("html_url") or ""
    name = repo.get("name")
    ref = (event or {}).get("ref") or ""
    return {
        "source_type": "repo",
        "repo_url": str(clone).removesuffix(".git"),
        "name": name,
        "incremental": True,
        "resume": True,
        "ref": ref,
    }


def hook_secret() -> str:
    return (os.getenv("GITHUB_WEBHOOK_SECRET") or "").strip()
