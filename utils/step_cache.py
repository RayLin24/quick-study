"""Content-key cache for Identify / relationships (incremental regen).

Hashes crawled files plus step params. Unchanged inputs reuse the last
structured result and skip the LLM. Prompt-level llm_cache/ is separate.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CACHE_NAME = "step_cache.json"
IDENTIFY = "identify"
RELATIONSHIPS = "relationships"


def files_fingerprint(files) -> str:
    digest = hashlib.sha256()
    for path, content in files or []:
        digest.update(str(path).encode("utf-8"))
        digest.update(b"\0")
        digest.update((content or "").encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def content_key(step: str, files, extra: dict | None = None) -> str:
    payload = {
        "step": step,
        "files": files_fingerprint(files),
        "extra": extra or {},
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_path(folder: Path) -> Path:
    return Path(folder) / CACHE_NAME


def load_step(folder: Path, step: str, key: str):
    path = cache_path(folder)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    entry = data.get(step)
    if not isinstance(entry, dict):
        return None
    if entry.get("key") != key:
        return None
    return entry.get("payload")


def save_step(folder: Path, step: str, key: str, payload) -> None:
    root = Path(folder)
    root.mkdir(parents=True, exist_ok=True)
    path = cache_path(root)
    data = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    data[step] = {"key": key, "payload": payload}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def identify_extra(shared: dict) -> dict:
    return {
        "max_abstraction_num": shared.get("max_abstraction_num", 10),
        "language": (shared.get("language") or "english").lower(),
        "learning_goal": shared.get("learning_goal") or "",
        "seed_files": list(shared.get("seed_files") or []),
    }


def relationships_extra(shared: dict) -> dict:
    names = []
    for item in shared.get("abstractions") or []:
        names.append(
            {
                "name": item.get("name"),
                "files": list(item.get("files") or []),
            }
        )
    return {
        "language": (shared.get("language") or "english").lower(),
        "abstractions": names,
    }


def use_step_cache(shared: dict) -> bool:
    if shared.get("incremental"):
        return True
    return bool(shared.get("use_cache", True))
