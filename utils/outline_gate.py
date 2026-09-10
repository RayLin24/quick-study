"""Confirm the identified outline before parallel chapter writes.

Precheck (crawl-only) is not enough: this gate runs after Identify +
relationships + order, shows the abstraction list and remaining LLM calls,
and waits for UI/API/TTY confirm when enabled.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from utils.preview import estimate_llm_calls


def confirm_enabled(explicit: bool | None = None) -> bool:
    if explicit is not None:
        return bool(explicit)
    raw = (os.getenv("OUTLINE_CONFIRM") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return False


def gate_paths(output_dir: str | Path) -> tuple[Path, Path]:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / ".outline_gate.json", root / ".outline_confirm"


def build_outline_payload(shared: dict) -> dict:
    abstractions = shared.get("abstractions") or []
    order = shared.get("chapter_order") or list(range(len(abstractions)))
    items = []
    for index in order:
        if not (0 <= index < len(abstractions)):
            continue
        abstr = abstractions[index]
        files = abstr.get("files") or []
        items.append(
            {
                "index": index,
                "name": abstr.get("name"),
                "description": (abstr.get("description") or "").strip().split("\n")[0][:200],
                "files": files if all(isinstance(x, str) for x in files) else [str(x) for x in files],
            }
        )
    estimates = estimate_llm_calls(max(len(items), 1))
    remaining = {
        "write_chapters": len(items),
        "combine": 0,
        "remaining": len(items),
        "note": "Identify / relationships / order 已完成；确认后并行写章。",
    }
    return {
        "project_name": shared.get("project_name"),
        "language": shared.get("language"),
        "abstractions": items,
        "chapter_count": len(items),
        "estimated_calls": estimates,
        "remaining_calls": remaining,
        "output_dir": shared.get("output_dir"),
        "confirm": confirm_enabled(shared.get("confirm_outline")),
    }


def write_outline_gate(output_dir: str | Path, payload: dict) -> Path:
    data_path, _confirm = gate_paths(output_dir)
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("QUICK_STUDY_STEP: outline", flush=True)
    print(f"QUICK_STUDY_OUTLINE: waiting=1 path={data_path}", flush=True)
    return data_path


def load_outline_gate(output_dir: str | Path) -> dict | None:
    data_path, confirm_path = gate_paths(output_dir)
    if not data_path.is_file():
        return None
    try:
        payload = json.loads(data_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    payload["confirmed"] = confirm_path.is_file()
    payload["awaiting"] = not confirm_path.is_file()
    return payload


def confirm_outline(output_dir: str | Path) -> dict:
    data_path, confirm_path = gate_paths(output_dir)
    confirm_path.write_text("ok\n", encoding="utf-8")
    print("QUICK_STUDY_OUTLINE: waiting=0 confirmed=1", flush=True)
    payload = load_outline_gate(output_dir) or {}
    payload["confirmed"] = True
    payload["awaiting"] = False
    payload["path"] = str(data_path)
    return payload


def clear_outline_gate(output_dir: str | Path) -> None:
    for path in gate_paths(output_dir):
        if path.exists():
            path.unlink()


def wait_for_outline_confirm(
    output_dir: str | Path,
    payload: dict,
    *,
    enabled: bool,
    poll: float = 0.25,
    timeout: float | None = None,
    prompt_tty: bool | None = None,
) -> dict:
    write_outline_gate(output_dir, payload)
    if not enabled:
        return confirm_outline(output_dir)
    use_tty = sys.stdin.isatty() if prompt_tty is None else prompt_tty
    if use_tty:
        print("大纲确认（写章前）", flush=True)
        for item in payload.get("abstractions") or []:
            print(f"  - {item.get('name')}: {item.get('description') or ''}", flush=True)
        remaining = (payload.get("remaining_calls") or {}).get("remaining")
        print(f"预计剩余 LLM 调用: {remaining}", flush=True)
        answer = input("确认后并行写章？[y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            raise RuntimeError("大纲未确认，已中止写章")
        return confirm_outline(output_dir)

    deadline = time.monotonic() + (timeout if timeout is not None else float(os.getenv("OUTLINE_CONFIRM_TIMEOUT", "86400")))
    _, confirm_path = gate_paths(output_dir)
    while not confirm_path.is_file():
        if time.monotonic() > deadline:
            raise TimeoutError("等待大纲确认超时")
        from utils.job_control import wait_if_paused

        wait_if_paused(Path(output_dir), poll=poll, timeout=poll)
        time.sleep(poll)
    return load_outline_gate(output_dir) or payload
