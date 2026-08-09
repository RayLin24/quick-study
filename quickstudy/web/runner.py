"""流水线执行器：asyncio 串行 worker（design §2/§3）。

PRE_GATE 四段跑完停在 awaiting_confirm（成本闸门）；confirm 后才跑 writing。
取消不硬杀阶段，阶段间检查点生效；awaiting_confirm 放弃立即生效。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from quickstudy.config import TaskConfig
from quickstudy.web.jobs import JobStore

log = logging.getLogger(__name__)
StageFn = Callable[[TaskConfig], Awaitable[object]]


def default_stage_fns() -> dict[str, StageFn]:
    from quickstudy.pipeline import run_m1
    from quickstudy.pipeline_m2 import run_m2
    from quickstudy.pipeline_m3 import run_m3
    from quickstudy.pipeline_m4 import run_book, run_outline

    return {"crawling": run_m1,
            "organizing": lambda cfg: run_m2(cfg),
            "demoing": lambda cfg: run_m3(cfg),
            "outlining": run_outline,
            "writing": lambda cfg: run_book(cfg)}


class Runner:
    PRE_GATE = ["crawling", "organizing", "demoing", "outlining"]

    def __init__(self, store: JobStore, stage_fns: dict[str, StageFn] | None = None,
                 docker_check: Callable[[], bool] | None = None):
        self.store = store
        self.stage_fns = stage_fns or default_stage_fns()
        if docker_check is None:
            from quickstudy.demo.sandbox import docker_available
            docker_check = docker_available
        self.docker_check = docker_check
        self._lock = asyncio.Lock()

    def _cfg(self, job: dict) -> TaskConfig:
        return TaskConfig.load(job["url"], workspace=str(self.store.root),
                               max_pages=job.get("max_pages"))

    def _attach_log(self, job: dict) -> logging.Handler:
        task_dir = self.store.task_dir(job)
        task_dir.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(task_dir / "job.log", encoding="utf-8")
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logging.getLogger("quickstudy").addHandler(h)
        return h

    @staticmethod
    def _detach_log(h: logging.Handler) -> None:
        logging.getLogger("quickstudy").removeHandler(h)
        h.close()

    async def run_next(self) -> dict | None:
        if self._lock.locked() or self.store.find_running():
            return None
        job = self.store.next_queued()
        if job is None:
            return None
        async with self._lock:
            return await self._run_pre_gate(job["id"])

    async def _run_pre_gate(self, job_id: str) -> dict:
        handler = None
        try:
            for stage in self.PRE_GATE:
                job = self.store.get(job_id)
                if job["cancel_requested"]:
                    return self.store.update(job_id, status="cancelled")
                if stage == "demoing" and (not job["with_demos"]
                                           or not self.docker_check()):
                    self.store.update(job_id, skipped_demos=True)
                    continue
                self.store.update(job_id, status=stage)
                if handler is None:
                    handler = self._attach_log(self.store.get(job_id))
                await self.stage_fns[stage](self._cfg(self.store.get(job_id)))
            return self.store.update(job_id, status="awaiting_confirm")
        except Exception as e:  # noqa: BLE001 - 任何阶段异常都落为 failed
            log.exception("任务 %s 失败", job_id)
            return self.store.update(job_id, status="failed",
                                     error=f"{type(e).__name__}: {e}")
        finally:
            if handler is not None:
                self._detach_log(handler)

    async def confirm(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] != "awaiting_confirm":
            raise ValueError(f"任务状态 {job['status']} 不能确认")
        if self._lock.locked():
            raise RuntimeError("另一任务正在运行")
        async with self._lock:
            handler = self._attach_log(job)
            try:
                self.store.update(job_id, status="writing")
                await self.stage_fns["writing"](self._cfg(job))
                return self.store.update(job_id, status="done")
            except Exception as e:  # noqa: BLE001
                log.exception("任务 %s 写书失败", job_id)
                return self.store.update(job_id, status="failed",
                                         error=f"{type(e).__name__}: {e}")
            finally:
                self._detach_log(handler)

    def request_cancel(self, job_id: str) -> dict:
        job = self.store.get(job_id)
        if job is None:
            raise KeyError(job_id)
        if job["status"] == "awaiting_confirm":
            return self.store.update(job_id, status="cancelled")
        return self.store.update(job_id, cancel_requested=True)
