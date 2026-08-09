"""Web 任务注册表：jobs.json 持久化 + 状态机常量（design §3）。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

STAGES = ["crawling", "organizing", "demoing", "outlining", "writing"]
RUNNING_STATES = {"queued", *STAGES}       # 进程崩溃时需清扫的范围
TERMINAL_STATES = {"done", "failed", "cancelled"}


class JobStore:
    def __init__(self, workspace_root: Path | str = "workspace"):
        self.root = Path(workspace_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "jobs.json"

    def _load(self) -> list[dict]:
        if not self.db_path.exists():
            return []
        return json.loads(self.db_path.read_text(encoding="utf-8"))

    def _save(self, jobs: list[dict]) -> None:
        self.db_path.write_text(json.dumps(jobs, ensure_ascii=False, indent=2),
                                encoding="utf-8")

    def create(self, url: str, with_demos: bool = True,
               max_pages: int | None = None) -> dict:
        from quickstudy.urltools import url_to_task_id

        jobs = self._load()
        job = {"id": uuid.uuid4().hex[:12], "url": url,
               "task_id": url_to_task_id(url), "status": "queued",
               "with_demos": with_demos, "max_pages": max_pages,
               "created_at": time.time(), "updated_at": time.time(),
               "error": "", "skipped_demos": False, "interrupted": False,
               "cancel_requested": False}
        jobs.append(job)
        self._save(jobs)
        return job

    def list(self) -> list[dict]:
        return sorted(self._load(), key=lambda j: -j["created_at"])

    def get(self, job_id: str) -> dict | None:
        return next((j for j in self._load() if j["id"] == job_id), None)

    def update(self, job_id: str, **fields) -> dict:
        jobs = self._load()
        for j in jobs:
            if j["id"] == job_id:
                j.update(fields, updated_at=time.time())
                self._save(jobs)
                return j
        raise KeyError(job_id)

    def find_running(self) -> dict | None:
        """正占用 worker 的任务（仅进行中的阶段；queued 是等待、awaiting_confirm 是挂起）。"""
        return next((j for j in self._load() if j["status"] in STAGES), None)

    def next_queued(self) -> dict | None:
        qs = [j for j in self._load()
              if j["status"] == "queued" and not j["cancel_requested"]]
        return min(qs, key=lambda j: j["created_at"]) if qs else None

    def mark_interrupted(self) -> int:
        """服务启动时调用：清扫崩溃遗留的 queued/进行中任务为 failed+interrupted。

        queued 也清扫：重启后没有确定性的驱动会捡起它，标失败引导用户重建，
        避免新任务触发 run_next 时旧 queued 任务 FIFO 插队的意外。
        """
        jobs = self._load()
        n = 0
        for j in jobs:
            if j["status"] in RUNNING_STATES:
                j.update(status="failed", interrupted=True,
                         error="服务中断，可同 URL 新建任务续跑")
                n += 1
        if n:
            self._save(jobs)
        return n

    def task_dir(self, job: dict) -> Path:
        return self.root / job["task_id"]
