from __future__ import annotations

import inspect
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from utils.allow_dir import assert_allowed_local_dir
from utils.budget import BudgetExceeded, assert_budget, record_tokens
from utils.errors import format_error
from utils.gitlab_gitea import classify_repo_url
from utils.job_control import is_paused, pause_process, request_pause, request_resume, resume_process
from utils.job_history import append_history, list_history
from utils.job_queue import dequeue, enqueue, load_queue
from utils.language import DEFAULT_LANGUAGE, normalize_language
from utils.partial import expected_output_name, isolate_cancelled_output
from utils.outline_gate import confirm_outline, load_outline_gate
from utils.provider_cost import estimate_from_usage
from utils.redact import looks_like_secret_key, redact_lines, redact_text
from utils.strategy import apply_strategy

GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
    r"(?:\.git)?(?:/tree/[^?\s#]+)?/?$",
    re.IGNORECASE,
)
COMPLETE_RE = re.compile(r"Tutorial generation complete! Files are in:\s*(.+)\s*$")
OUTPUT_NAME_RE = re.compile(r"QUICK_STUDY_OUTPUT:\s*(?P<name>\S+)")
STATS_RE = re.compile(
    r"QUICK_STUDY_STATS:\s*file_count=(?P<file_count>\d+)\s+map_mode=(?P<map_mode>\S+)"
)
STEP_RE = re.compile(r"QUICK_STUDY_STEP:\s*(?P<step>\w+)")
USAGE_RE = re.compile(
    r"QUICK_STUDY_USAGE:\s*prompt=(?P<prompt>\d+)\s+completion=(?P<completion>\d+)\s+"
    r"total=(?P<total>\d+)(?:\s+calls=(?P<calls>\d+))?(?:\s+max_tokens=(?P<max_tokens>\d+))?"
    r"(?P<rest>.*)$"
)
RETRY_AFTER_RE = re.compile(r"QUICK_STUDY_RETRY_AFTER:\s*(?P<seconds>\d+)")
STEP_ORDER = ("fetch", "identify", "relationships", "order", "outline", "write", "combine")
OUTLINE_RE = re.compile(r"QUICK_STUDY_OUTLINE:\s*(?P<body>.+)$")
LOG_RING = int(os.getenv("JOB_LOG_RING", "2000"))
DEFAULT_JOB_TIMEOUT = float(os.getenv("JOB_TIMEOUT_SECONDS", "3600"))

Runner = Callable[..., int]


class JobBusyError(Exception):
    pass


class JobCancelled(Exception):
    pass


def _split_patterns(raw) -> Optional[list[str]]:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple, set)):
        items = [str(item).strip() for item in raw if str(item).strip()]
        return items or None
    text = str(raw).strip()
    if not text:
        return None
    return [part.strip() for part in re.split(r"[\s,]+", text) if part.strip()]


def validate_start_request(payload: dict) -> dict:
    source_type = str(payload.get("source_type") or "").strip()
    language = normalize_language(payload.get("language") or DEFAULT_LANGUAGE)
    payload = apply_strategy(payload, payload.get("strategy")) if payload.get("strategy") else payload
    name = str(payload.get("name") or "").strip() or None
    token = str(payload.get("github_token") or "").strip() or None
    include = _split_patterns(payload.get("include"))
    exclude = _split_patterns(payload.get("exclude"))
    try:
        max_abstractions = int(payload.get("max_abstractions") or 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_abstractions 必须是整数") from exc
    if max_abstractions < 1 or max_abstractions > 20:
        raise ValueError("max_abstractions 范围是 1–20")
    max_size = payload.get("max_size")
    if max_size in ("", None):
        max_size = None
    else:
        try:
            max_size = int(max_size)
        except (TypeError, ValueError) as exc:
            raise ValueError("max_size 必须是整数") from exc
        if max_size < 1:
            raise ValueError("max_size 必须大于 0")

    timeout = payload.get("timeout")
    if timeout in ("", None):
        timeout_seconds = DEFAULT_JOB_TIMEOUT
    else:
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError) as exc:
            raise ValueError("timeout 必须是数字") from exc

    common = {
        "language": language,
        "name": name,
        "github_token": token,
        "max_abstractions": max_abstractions,
        "include": include,
        "exclude": exclude,
        "max_size": max_size,
        "include_specified": bool(include),
        "timeout": timeout_seconds,
        "resume": bool(payload.get("resume") or payload.get("incremental")),
        "incremental": bool(payload.get("incremental")),
        "overview_only": bool(payload.get("overview_only")),
        "polish": bool(payload.get("polish")),
        "replace": bool(payload.get("replace")),
        "strategy": str(payload.get("strategy") or "").strip() or None,
        "learning_goal": str(payload.get("learning_goal") or "").strip() or None,
        "bilingual": bool(payload.get("bilingual")),
        "pagerank_order": bool(payload.get("pagerank_order")),
        "seed_files": payload.get("seed_files") or None,
        "queue": bool(payload.get("queue")),
        "retry_chapter": str(payload.get("retry_chapter") or "").strip() or None,
        "confirm_outline": payload.get("confirm_outline")
        if payload.get("confirm_outline") is not None
        else os.getenv("OUTLINE_CONFIRM", "1").strip() not in {"0", "false", "no", "off"},
    }

    if source_type == "repo":
        repo_url = str(payload.get("repo_url") or "").strip()
        kind = classify_repo_url(repo_url)
        if not kind and not GITHUB_REPO_RE.match(repo_url):
            raise ValueError(
                "请填写有效的 GitHub / GitLab / Gitea 仓库 URL，例如 https://github.com/owner/repo "
                "或 https://gitlab.com/group/proj"
            )
        return {
            "source_type": "repo",
            "repo_url": repo_url.rstrip("/"),
            "local_dir": None,
            **common,
        }

    if source_type == "dir":
        raw = str(payload.get("local_dir") or "").strip()
        path = Path(raw).expanduser()
        if not path.is_dir():
            raise ValueError("本地目录不存在，或路径不是目录")
        assert_allowed_local_dir(path)
        return {
            "source_type": "dir",
            "repo_url": None,
            "local_dir": str(path.resolve()),
            **common,
        }

    raise ValueError("请选择 GitHub 仓库或本地目录")


def build_command(
    payload: dict,
    *,
    python_exe: str,
    main_py: Path,
    output_dir: Path,
) -> list[str]:
    data = validate_start_request(payload)
    cmd = [python_exe, str(main_py)]
    if data["source_type"] == "repo":
        cmd += ["--repo", data["repo_url"]]
    else:
        cmd += ["--dir", data["local_dir"]]
    cmd += ["--language", data["language"], "--output", str(output_dir)]
    if data["name"]:
        cmd += ["--name", data["name"]]
    # Token stays in the child environment, never on argv.
    if data["include"]:
        cmd += ["--include", *data["include"]]
    if data["exclude"]:
        cmd += ["--exclude", *data["exclude"]]
    if data["max_size"]:
        cmd += ["--max-size", str(data["max_size"])]
    cmd += ["--max-abstractions", str(data["max_abstractions"])]
    if data.get("resume"):
        cmd.append("--resume")
    if data.get("incremental"):
        cmd.append("--incremental")
    if data.get("overview_only"):
        cmd.append("--overview-only")
    if data.get("polish"):
        cmd.append("--polish")
    if data.get("strategy"):
        cmd += ["--strategy", data["strategy"]]
    if data.get("learning_goal"):
        cmd += ["--learning-goal", data["learning_goal"]]
    if data.get("bilingual"):
        cmd.append("--bilingual")
    if data.get("pagerank_order"):
        cmd.append("--pagerank-order")
    if data.get("seed_files"):
        seeds = data["seed_files"] if isinstance(data["seed_files"], list) else [data["seed_files"]]
        cmd += ["--seed", *[str(item) for item in seeds if str(item).strip()]]
    if data.get("confirm_outline"):
        cmd.append("--confirm-outline")
    else:
        cmd.append("--skip-outline-confirm")
    return cmd


def kill_process_group(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.terminate()
        except OSError:
            return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        proc.wait(timeout=3)


def subprocess_runner(cmd: list[str], on_line: Callable[[str], None], cwd: Path, job: Optional["Job"] = None) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    token = None
    if job is not None:
        token = job.payload.get("github_token")
    if token:
        env["GITHUB_TOKEN"] = token
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        start_new_session=True,
    )
    if job is not None:
        job.proc = proc
    assert proc.stdout is not None
    deadline = None
    if job is not None and job.timeout and job.timeout > 0:
        deadline = time.monotonic() + job.timeout
    try:
        for line in proc.stdout:
            if job is not None and job.cancelled:
                kill_process_group(proc)
                break
            if deadline is not None and time.monotonic() > deadline:
                kill_process_group(proc)
                on_line(format_error(f"job timed out after {job.timeout:.0f}s"))
                break
            on_line(line.rstrip("\n"))
        code = proc.wait()
        if job is not None and job.cancelled:
            raise JobCancelled("任务已取消")
        if deadline is not None and proc.returncode not in (0, None) and job is not None and not job.cancelled:
            if "timed out" in "\n".join(job.logs[-5:]):
                return code if code is not None else 124
        return code
    finally:
        if proc.poll() is None:
            kill_process_group(proc)


class Job:
    def __init__(self, payload: dict, timeout: float):
        self.id = uuid.uuid4().hex[:12]
        self.payload = payload
        self.status = "running"
        self.logs: list[str] = []
        self.log_start = 0
        self.log_end = 0
        self.error: Optional[str] = None
        self.output_name: Optional[str] = None
        self.file_count: Optional[int] = None
        self.map_mode: Optional[bool] = None
        self.step: Optional[str] = None
        self.usage: Optional[dict] = None
        self.retry_after: Optional[int] = None
        self.timeout = timeout
        self.cancelled = False
        self.paused = False
        self.awaiting_outline = False
        self.outline: Optional[dict] = None
        self.started_at = time.time()
        self.proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def append_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)
            self.log_end += 1
            if len(self.logs) > LOG_RING:
                drop = len(self.logs) - LOG_RING
                del self.logs[:drop]
                self.log_start += drop
            match = STATS_RE.search(line)
            if match:
                self.file_count = int(match.group("file_count"))
                flag = match.group("map_mode").lower()
                if flag != "pending":
                    self.map_mode = flag in ("true", "1", "yes")
            step_match = STEP_RE.search(line)
            if step_match and step_match.group("step") in STEP_ORDER:
                self.step = step_match.group("step")
            output_match = OUTPUT_NAME_RE.search(line)
            if output_match and not self.output_name:
                self.output_name = output_match.group("name")
            usage_match = USAGE_RE.search(line)
            if usage_match:
                rest = usage_match.group("rest") or ""
                stages = {}
                stage_blob = re.search(r"stages=(\S+)", rest)
                if stage_blob:
                    for part in stage_blob.group(1).split(","):
                        if ":" in part:
                            name, count = part.split(":", 1)
                            try:
                                stages[name] = int(count)
                            except ValueError:
                                continue
                self.usage = {
                    "prompt_tokens": int(usage_match.group("prompt")),
                    "completion_tokens": int(usage_match.group("completion")),
                    "total_tokens": int(usage_match.group("total")),
                    "calls": int(usage_match.group("calls") or 0),
                    "max_tokens": (
                        int(usage_match.group("max_tokens"))
                        if usage_match.group("max_tokens")
                        else None
                    ),
                    "by_stage": stages,
                    "unknown": "unknown=1" in rest or (
                        int(usage_match.group("total")) == 0
                        and int(usage_match.group("calls") or 0) > 0
                    ),
                }
            retry_match = RETRY_AFTER_RE.search(line)
            if retry_match:
                self.retry_after = int(retry_match.group("seconds"))
            outline_match = OUTLINE_RE.search(line)
            if outline_match:
                body = outline_match.group("body") or ""
                self.awaiting_outline = "waiting=1" in body
                if "confirmed=1" in body or "waiting=0" in body:
                    self.awaiting_outline = False

    def request_cancel(self) -> None:
        self.cancelled = True
        if self.proc is not None:
            kill_process_group(self.proc)

    def finish(self, *, success: bool, output_name: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            if success:
                self.status = "succeeded"
            elif self.cancelled:
                self.status = "cancelled"
            else:
                self.status = "failed"
            self.output_name = output_name
            self.error = format_error(error) if error else error

    def snapshot(self, after: Optional[int] = None) -> dict:
        with self._lock:
            if after is None or after < self.log_start:
                lines = list(self.logs)
                cursor_from = self.log_start
            else:
                offset = after - self.log_start
                lines = list(self.logs[offset:])
                cursor_from = after
            return {
                "id": self.id,
                "status": self.status,
                "logs": lines,
                "log_start": self.log_start,
                "log_cursor": self.log_end,
                "log_from": cursor_from,
                "error": self.error,
                "output_name": self.output_name,
                "file_count": self.file_count,
                "map_mode": self.map_mode,
                "step": self.step,
                "usage": self.usage,
                "retry_after": self.retry_after,
                "source_type": self.payload.get("source_type"),
                "paused": self.paused,
                "awaiting_outline": self.awaiting_outline,
                "outline": self.outline,
                "cost": estimate_from_usage(self.usage) if self.usage else None,
                "started_at": self.started_at,
            }

    def persist_payload(self) -> dict:
        data = {
            k: v
            for k, v in self.payload.items()
            if k != "github_token" and not looks_like_secret_key(str(k))
        }
        snap = self.snapshot()
        snap["logs"] = redact_lines(snap.get("logs") or [])
        snap["error"] = redact_text(snap.get("error") or "") or None
        snap["payload"] = data
        return snap


class JobManager:
    def __init__(
        self,
        *,
        output_dir: Path,
        python_exe: str,
        main_py: Path,
        cwd: Path,
        runner: Runner = subprocess_runner,
    ):
        self.output_dir = Path(output_dir)
        self.python_exe = python_exe
        self.main_py = Path(main_py)
        self.cwd = Path(cwd)
        self._runner = runner
        self._lock = threading.Lock()
        self._job: Optional[Job] = None
        self._thread: Optional[threading.Thread] = None
        self.state_path = self.output_dir / "current.json"

    def snapshot(self, after: Optional[int] = None) -> dict:
        with self._lock:
            if self._job is None:
                return {
                    "status": "idle",
                    "logs": [],
                    "error": None,
                    "output_name": None,
                    "id": None,
                    "log_start": 0,
                    "log_cursor": 0,
                    "file_count": None,
                    "map_mode": None,
                    "step": None,
                    "usage": None,
                    "retry_after": None,
                    "paused": False,
                    "awaiting_outline": False,
                    "outline": None,
                    "cost": None,
                    "queue": load_queue(self.output_dir),
                }
            snap = self._job.snapshot(after=after)
            snap["queue"] = load_queue(self.output_dir)
            loaded = load_outline_gate(self.output_dir)
            if loaded:
                self._job.outline = loaded
                self._job.awaiting_outline = bool(loaded.get("awaiting"))
                snap["outline"] = loaded
                snap["awaiting_outline"] = bool(loaded.get("awaiting"))
            return snap

    def start(self, payload: dict) -> dict:
        data = validate_start_request(payload)
        assert_budget(self.output_dir)
        if data.get("queue") and not data.get("replace"):
            with self._lock:
                busy = self._job is not None and self._job.status == "running"
            if busy:
                item = enqueue(self.output_dir, data)
                return {
                    "status": "queued",
                    "queued": True,
                    "id": item["id"],
                    "position": item["position"],
                    "queue": load_queue(self.output_dir),
                }
        if data.get("replace"):
            with self._lock:
                current = self._job
                running = current is not None and current.status == "running"
            if running:
                current.request_cancel()
                self.wait(timeout=30)
        cmd = build_command(
            data,
            python_exe=self.python_exe,
            main_py=self.main_py,
            output_dir=self.output_dir,
        )
        with self._lock:
            if self._job is not None and self._job.status == "running":
                raise JobBusyError("已有任务正在运行")
            job = Job(data, timeout=data["timeout"])
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(job, cmd), daemon=True)
            self._thread.start()
        self._persist(job)
        return job.snapshot()

    def cancel(self) -> dict:
        with self._lock:
            job = self._job
            if job is None or job.status != "running":
                raise ValueError("当前没有运行中的任务")
            job.request_cancel()
        request_resume(self.output_dir)
        return job.snapshot()

    def pause(self) -> dict:
        with self._lock:
            job = self._job
            if job is None or job.status != "running":
                raise ValueError("当前没有运行中的任务")
            job.paused = True
            pid = job.proc.pid if job.proc is not None else None
        request_pause(self.output_dir)
        pause_process(pid)
        job.append_log("QUICK_STUDY_PAUSE: 1")
        return job.snapshot()

    def resume(self) -> dict:
        with self._lock:
            job = self._job
            if job is None or job.status != "running":
                raise ValueError("当前没有运行中的任务")
            job.paused = False
            pid = job.proc.pid if job.proc is not None else None
        request_resume(self.output_dir)
        resume_process(pid)
        job.append_log("QUICK_STUDY_PAUSE: 0")
        return job.snapshot()

    def outline_snapshot(self) -> dict:
        loaded = load_outline_gate(self.output_dir) or {}
        with self._lock:
            job = self._job
            running = job is not None and job.status == "running"
            if job is not None and loaded:
                job.outline = loaded
                job.awaiting_outline = bool(loaded.get("awaiting"))
        return {
            "ok": bool(loaded),
            "awaiting": bool(loaded.get("awaiting")) if loaded else False,
            "running": running,
            "outline": loaded or None,
        }

    def confirm_current_outline(self) -> dict:
        with self._lock:
            job = self._job
            if job is None or job.status != "running":
                raise ValueError("当前没有运行中的任务")
        payload = confirm_outline(self.output_dir)
        job.awaiting_outline = False
        job.outline = payload
        job.append_log("QUICK_STUDY_OUTLINE: waiting=0 confirmed=1")
        return self.snapshot()

    def history(self, limit: int = 50) -> list[dict]:
        return list_history(self.output_dir, limit=limit)

    def queue_snapshot(self) -> list[dict]:
        return load_queue(self.output_dir)

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("任务仍在运行")

    def _invoke_runner(self, job: Job, cmd: list[str]) -> int:
        kwargs = {}
        try:
            if "job" in inspect.signature(self._runner).parameters:
                kwargs["job"] = job
        except (TypeError, ValueError):
            pass
        return self._runner(cmd, job.append_log, self.cwd, **kwargs)

    def _run(self, job: Job, cmd: list[str]) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            self._persist(job)
            code = self._invoke_runner(job, cmd)
            if job.cancelled:
                job.finish(success=False, error="任务已取消")
            elif code == 0:
                job.finish(
                    success=True,
                    output_name=_output_name_from_logs(job.logs, self.output_dir)
                    or _newest_tutorial_name(self.output_dir),
                )
            else:
                job.finish(success=False, error=_exit_reason(job.logs, code))
        except JobCancelled:
            job.finish(success=False, error="任务已取消")
        except Exception as exc:
            job.finish(success=False, error=str(exc))
        finally:
            if job.status == "cancelled":
                name = job.output_name or expected_output_name(job.payload)
                moved = isolate_cancelled_output(self.output_dir, name, job_id=job.id)
                if moved:
                    job.append_log(f"QUICK_STUDY_PARTIAL: {moved.as_posix()}")
            self._persist(job)
            if job.status in {"succeeded", "failed", "cancelled"}:
                if job.usage and job.usage.get("total_tokens"):
                    try:
                        record_tokens(self.output_dir, int(job.usage.get("total_tokens") or 0))
                    except Exception:
                        pass
                try:
                    append_history(self.output_dir, job.snapshot(), started_at=job.started_at)
                except Exception as exc:
                    job.append_log(f"QUICK_STUDY_WARN: history {exc}")
                try:
                    from utils.jsonl_log import emit_run

                    emit_run(
                        self.output_dir,
                        "job_done",
                        status=job.status,
                        id=job.id,
                        output_name=job.output_name,
                    )
                except Exception:
                    pass
                try:
                    from utils.webhook import notify_completion

                    notify_completion(job.snapshot())
                except Exception as exc:
                    job.append_log(f"QUICK_STUDY_WARN: webhook {exc}")
                self._drain_queue()

    def _drain_queue(self) -> None:
        item = dequeue(self.output_dir)
        if not item:
            return
        payload = item.get("payload") or {}
        try:
            self.start(payload)
        except (JobBusyError, ValueError, BudgetExceeded) as exc:
            enqueue(self.output_dir, payload)
            if self._job is not None:
                self._job.append_log(f"QUICK_STUDY_WARN: queue {exc}")

    def _persist(self, job: Job) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(job.persist_payload(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except OSError:
            pass


ERROR_HINTS = (
    "Traceback",
    "Error",
    "Exception",
    "ValueError",
    "KeyError",
    "OPENROUTER_API_KEY",
    "LLM_PROVIDER",
    "QUICK_STUDY_ERROR",
    "Failed",
    "failed",
    "HTTP error",
    "timed out",
    "超时",
)


def _exit_reason(logs: list[str], code: int) -> str:
    matched = [line.rstrip() for line in logs if line.strip() and any(hint in line for hint in ERROR_HINTS)]
    if matched:
        excerpt = "\n".join(matched[-8:])
        return format_error(f"进程退出码 {code}\n{excerpt}")
    nonempty = [line.rstrip() for line in logs if line.strip()]
    if nonempty:
        return format_error(f"进程退出码 {code}\n{nonempty[-1]}")
    return format_error(f"进程退出码 {code}")


def _output_name_from_logs(logs: list[str], output_dir: Path) -> Optional[str]:
    for line in reversed(logs):
        match = COMPLETE_RE.search(line)
        if not match:
            continue
        raw = Path(match.group(1).strip())
        try:
            return raw.resolve().relative_to(output_dir.resolve()).as_posix()
        except ValueError:
            return raw.name
    return None


def _newest_tutorial_name(output_dir: Path) -> Optional[str]:
    if not output_dir.is_dir():
        return None
    newest = None
    newest_mtime = -1.0
    for child in output_dir.iterdir():
        if child.name.startswith("."):
            continue
        index = child / "index.md"
        if child.is_dir() and index.is_file():
            mtime = index.stat().st_mtime
            if mtime > newest_mtime:
                newest = child.name
                newest_mtime = mtime
    return newest
