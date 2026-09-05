"""File-based admin audit log by token id / local session (feature 31). Not an account system."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path


def actor_id(*, token: str = "", session: str = "", ip: str = "") -> str:
    raw = (token or session or ip or "anon").encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    kind = "token" if token else ("session" if session else ("ip" if ip else "anon"))
    return f"{kind}:{digest}"


def audit_path(output_dir: Path) -> Path:
    return Path(output_dir) / "audit.jsonl"


def append_audit(output_dir: Path, *, action: str, actor: str, detail: dict | None = None) -> dict:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    rec = {
        "ts": int(time.time()),
        "action": action,
        "actor": actor,
        "detail": detail or {},
    }
    with audit_path(output_dir).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return rec


def list_audit(output_dir: Path, *, limit: int = 100) -> list[dict]:
    path = audit_path(output_dir)
    if not path.is_file():
        return []
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    items.reverse()
    return items[: max(1, int(limit))]
