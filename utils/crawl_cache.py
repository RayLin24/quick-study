"""Reuse a dry-run crawl so the real job does not hit GitHub twice."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

CACHE_DIR = Path(os.getenv("CRAWL_CACHE_DIR", "crawl_cache"))
MAX_AGE = float(os.getenv("CRAWL_CACHE_SECONDS", "3600"))


def crawl_cache_key(payload: dict) -> str:
    material = {
        "repo_url": payload.get("repo_url"),
        "local_dir": payload.get("local_dir"),
        "include": sorted(payload.get("include") or payload.get("include_patterns") or []),
        "exclude": sorted(payload.get("exclude") or payload.get("exclude_patterns") or []),
        "max_size": payload.get("max_size") or payload.get("max_file_size"),
        "has_token": bool(payload.get("github_token") or payload.get("token")),
    }
    raw = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def save_crawl_cache(payload: dict, files_list: list) -> str:
    key = crawl_cache_key(payload)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _path(key)
    tmp = path.with_suffix(".json.tmp")
    payload_out = {
        "key": key,
        "saved_at": time.time(),
        "files": [[path, content] for path, content in files_list],
    }
    tmp.write_text(json.dumps(payload_out, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)
    return key


def load_crawl_cache(payload: dict, *, max_age: float | None = None) -> list | None:
    key = crawl_cache_key(payload)
    path = _path(key)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    age = time.time() - float(data.get("saved_at") or 0)
    limit = MAX_AGE if max_age is None else max_age
    if age > limit:
        return None
    files = data.get("files")
    if not isinstance(files, list):
        return None
    return [(item[0], item[1]) for item in files if isinstance(item, list) and len(item) == 2]
