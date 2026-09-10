"""Pause/resume support matrix (#29). Cooperative checkpoint is the supported path."""

from __future__ import annotations

import os
import sys

from utils.job_control import is_paused, request_pause, request_resume


def sigstop_opt_in() -> bool:
    return os.getenv("QS_PAUSE_SIGSTOP", "0").lower() not in ("0", "false", "no")


def pause_matrix() -> dict:
    unix = sys.platform != "win32"
    return {
        "recommended": "cooperative",
        "cooperative": True,
        "sigstop_supported": unix,
        "sigstop_enabled": unix and sigstop_opt_in(),
        "platform": sys.platform,
        "matrix": [
            {
                "platform": "Linux/macOS",
                "cooperative": True,
                "sigstop": "opt-in QS_PAUSE_SIGSTOP=1（可能冻住进行中的 HTTP）",
            },
            {
                "platform": "Windows",
                "cooperative": True,
                "sigstop": False,
            },
        ],
        "note": "默认只写 .job_pause，写章节点 wait_if_paused。不把 SIGSTOP 当可移植语义。",
    }


def request_cooperative_pause(output_dir) -> dict:
    request_pause(output_dir)
    return {"paused": True, "mode": "cooperative", **pause_matrix()}


def request_cooperative_resume(output_dir) -> dict:
    request_resume(output_dir)
    return {"paused": is_paused(output_dir), "mode": "cooperative", **pause_matrix()}
