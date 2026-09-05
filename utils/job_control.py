"""Pause / resume a running generation without cancelling (feature 17)."""

from __future__ import annotations

import os
import signal
import time
from pathlib import Path


def pause_path(output_dir: Path) -> Path:
    return Path(output_dir) / ".job_pause"


def request_pause(output_dir: Path) -> None:
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    pause_path(output_dir).write_text("1", encoding="utf-8")


def request_resume(output_dir: Path) -> None:
    path = pause_path(output_dir)
    if path.exists():
        path.unlink()


def is_paused(output_dir: Path) -> bool:
    return pause_path(output_dir).is_file()


def wait_if_paused(output_dir: Path | None, *, poll: float = 0.2, timeout: float = 3600) -> bool:
    """Block while pause file exists. Returns True if a pause was observed."""
    if output_dir is None:
        return False
    seen = False
    deadline = time.monotonic() + timeout
    while is_paused(output_dir):
        seen = True
        if time.monotonic() > deadline:
            break
        time.sleep(poll)
    return seen


def signal_process_group(pid: int | None, sig: int) -> bool:
    if not pid:
        return False
    try:
        os.killpg(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.kill(pid, sig)
            return True
        except (ProcessLookupError, PermissionError, OSError):
            return False


def pause_process(pid: int | None) -> bool:
    return signal_process_group(pid, signal.SIGSTOP)


def resume_process(pid: int | None) -> bool:
    return signal_process_group(pid, signal.SIGCONT)
