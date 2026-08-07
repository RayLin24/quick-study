"""产物落盘：workspace 目录约定（design.md §7）+ 产物溯源元数据（ADR-006）。

所有中间产物带 _meta: {tool, version, input_hash}，支持断点续跑与结果缓存。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import quickstudy


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def input_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def artifact_meta(**inputs: Any) -> dict:
    """产物溯源元数据：工具版本 + 输入指纹。LLM 产物后续追加 prompt_version/model。"""
    return {"tool": f"quickstudy-{quickstudy.__version__}",
            "at": now_iso(),
            "input_hash": input_hash(inputs) if inputs else ""}


class Workspace:
    """workspace/{task_id}/ 目录句柄。"""

    def __init__(self, task_dir: Path):
        self.dir = Path(task_dir)
        for sub in ("raw", "parsed", "chunks", "demos", "chapters", "output"):
            (self.dir / sub).mkdir(parents=True, exist_ok=True)

    def path(self, *parts: str) -> Path:
        return self.dir.joinpath(*parts)

    def write_json(self, rel: str, data: Any) -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    def read_json(self, rel: str, default: Any = None) -> Any:
        p = self.path(rel)
        if not p.exists():
            return default
        return json.loads(p.read_text(encoding="utf-8"))

    def write_text(self, rel: str, text: str) -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def write_bytes(self, rel: str, data: bytes) -> Path:
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
