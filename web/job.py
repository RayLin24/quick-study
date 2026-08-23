from __future__ import annotations

import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Callable, Optional

GITHUB_REPO_RE = re.compile(
    r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?/?$",
    re.IGNORECASE,
)
COMPLETE_RE = re.compile(r"Tutorial generation complete! Files are in:\s*(.+)\s*$")

Runner = Callable[[list[str], Callable[[str], None], Path], int]


class JobBusyError(Exception):
    pass


def validate_start_request(payload: dict) -> dict:
    source_type = str(payload.get("source_type") or "").strip()
    language = str(payload.get("language") or "Chinese").strip() or "Chinese"
    name = str(payload.get("name") or "").strip() or None
    token = str(payload.get("github_token") or "").strip() or None
    try:
        max_abstractions = int(payload.get("max_abstractions") or 10)
    except (TypeError, ValueError) as exc:
        raise ValueError("max_abstractions 必须是整数") from exc
    if max_abstractions < 1 or max_abstractions > 20:
        raise ValueError("max_abstractions 范围是 1–20")

    if source_type == "repo":
        repo_url = str(payload.get("repo_url") or "").strip()
        if not GITHUB_REPO_RE.match(repo_url):
            raise ValueError("请填写有效的 GitHub 仓库 URL，例如 https://github.com/owner/repo")
        return {
            "source_type": "repo",
            "repo_url": repo_url.rstrip("/"),
            "local_dir": None,
            "language": language,
            "name": name,
            "github_token": token,
            "max_abstractions": max_abstractions,
        }

    if source_type == "dir":
        raw = str(payload.get("local_dir") or "").strip()
        path = Path(raw).expanduser()
        if not path.is_dir():
            raise ValueError("本地目录不存在，或路径不是目录")
        return {
            "source_type": "dir",
            "repo_url": None,
            "local_dir": str(path.resolve()),
            "language": language,
            "name": name,
            "github_token": token,
            "max_abstractions": max_abstractions,
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
    if data["github_token"]:
        cmd += ["--token", data["github_token"]]
    cmd += ["--max-abstractions", str(data["max_abstractions"])]
    return cmd


def subprocess_runner(cmd: list[str], on_line: Callable[[str], None], cwd: Path) -> int:
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        on_line(line.rstrip("\n"))
    return proc.wait()


class Job:
    def __init__(self, payload: dict):
        self.id = uuid.uuid4().hex[:12]
        self.payload = payload
        self.status = "running"
        self.logs: list[str] = []
        self.error: Optional[str] = None
        self.output_name: Optional[str] = None
        self._lock = threading.Lock()

    def append_log(self, line: str) -> None:
        with self._lock:
            self.logs.append(line)

    def finish(self, *, success: bool, output_name: Optional[str] = None, error: Optional[str] = None) -> None:
        with self._lock:
            self.status = "succeeded" if success else "failed"
            self.output_name = output_name
            self.error = error

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "status": self.status,
                "logs": list(self.logs),
                "error": self.error,
                "output_name": self.output_name,
                "source_type": self.payload.get("source_type"),
            }


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

    def snapshot(self) -> dict:
        with self._lock:
            if self._job is None:
                return {"status": "idle", "logs": [], "error": None, "output_name": None, "id": None}
            return self._job.snapshot()

    def start(self, payload: dict) -> dict:
        data = validate_start_request(payload)
        cmd = build_command(
            data,
            python_exe=self.python_exe,
            main_py=self.main_py,
            output_dir=self.output_dir,
        )
        with self._lock:
            if self._job is not None and self._job.status == "running":
                raise JobBusyError("已有任务正在运行")
            job = Job(data)
            self._job = job
            self._thread = threading.Thread(target=self._run, args=(job, cmd), daemon=True)
            self._thread.start()
        return job.snapshot()

    def wait(self, timeout: Optional[float] = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("任务仍在运行")

    def _run(self, job: Job, cmd: list[str]) -> None:
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)
            code = self._runner(cmd, job.append_log, self.cwd)
            if code == 0:
                job.finish(success=True, output_name=_output_name_from_logs(job.logs, self.output_dir))
            else:
                job.finish(success=False, error=f"进程退出码 {code}")
        except Exception as exc:
            job.finish(success=False, error=str(exc))


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
