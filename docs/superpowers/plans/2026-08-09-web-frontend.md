# Web 前端实施计划（输入官网地址 → 生成中文学习手册）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 quickstudy 增加 Web 界面：输入官方文档站 URL → 后端串行跑 M1-M4 流水线 → 大纲闸门确认 → 在线阅读生成的手册。

**Architecture:** FastAPI 后端（`quickstudy/web/`：jobs.py 状态机 + runner.py 串行执行器 + api.py 薄路由），Vue 3 + Element Plus 前端（`web/`，Vite 构建，hash 路由），生产模式由 FastAPI 托管 `web/dist`。流水线函数零改动复用。

**Tech Stack:** Python 3.11+ / FastAPI / uvicorn / pytest+httpx ASGI；Vue 3 / Element Plus / vue-router(hash) / markdown-it + highlight.js / Vite。

**Spec:** `docs/superpowers/specs/2026-08-09-web-frontend-design.md`

## Global Constraints

- 既有流水线代码（pipeline*.py、quickstudy/writer 等）**零改动**；Web 层只做编排与展示
- 单任务串行：同一时刻最多一个任务占用 worker；`awaiting_confirm` 挂起不占 worker
- 进度一律从产物推导（manifest/graph/outline/chapters/state/llm_cost），不解析日志
- 前端文案全中文；路由用 hash 模式（`createWebHashHistory`），服务端无需 SPA fallback
- 服务绑定 `127.0.0.1:8600`（本地自用，不暴露局域网）
- 后端每个任务 TDD（先写失败测试）；前端 v1 不写单测，手动验收
- 每个 Task 结束独立 commit，message 用简洁英文
- 运行测试统一：`.venv\Scripts\python.exe -m pytest tests/ -q`

---

### Task 1: JobStore 任务注册表与状态机

**Files:**
- Create: `quickstudy/web/__init__.py`（空文件）
- Create: `quickstudy/web/jobs.py`
- Test: `tests/test_web_jobs.py`

**Interfaces:**
- Produces（后续所有任务依赖）:
  - 模块常量 `STAGES = ["crawling", "organizing", "demoing", "outlining", "writing"]`、`RUNNING_STATES = {"queued", *STAGES}`、`TERMINAL_STATES = {"done", "failed", "cancelled"}`
  - `JobStore(workspace_root: Path | str)`，方法：
    - `create(url: str, with_demos: bool = True, max_pages: int | None = None) -> dict`
    - `list() -> list[dict]`（按 created_at 倒序）
    - `get(job_id: str) -> dict | None`
    - `update(job_id: str, **fields) -> dict`（自动刷新 updated_at；不存在抛 KeyError）
    - `find_running() -> dict | None`（status ∈ RUNNING_STATES）
    - `next_queued() -> dict | None`（最早 created_at 的 queued 且未 cancel_requested）
    - `mark_interrupted() -> int`（运行态 → failed + interrupted=True，返回标记数）
    - `task_dir(job: dict) -> Path`
  - job dict 字段：`id, url, task_id, status, with_demos, max_pages, created_at, updated_at, error, skipped_demos, interrupted, cancel_requested`

- [ ] **Step 1: 写失败测试**

```python
"""Web 任务注册表单测。"""
import pytest
from quickstudy.web.jobs import JobStore


def test_create_and_get(tmp_path):
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com", with_demos=False, max_pages=50)
    assert j["status"] == "queued" and j["with_demos"] is False
    assert j["task_id"] == "fastapi-tiangolo-com"
    got = s.get(j["id"])
    assert got["url"] == j["url"] and got["cancel_requested"] is False


def test_list_order_and_update(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    b = s.create("https://b.com")
    s.update(a["id"], status="crawling")
    jobs = s.list()
    assert jobs[0]["id"] == b["id"]          # 新建在前
    assert s.get(a["id"])["status"] == "crawling"
    with pytest.raises(KeyError):
        s.update("nonexistent", status="done")


def test_find_running_and_next_queued(tmp_path):
    s = JobStore(tmp_path)
    assert s.find_running() is None and s.next_queued() is None
    a = s.create("https://a.com")
    b = s.create("https://b.com")
    assert s.next_queued()["id"] == a["id"]   # 先来先跑
    s.update(a["id"], status="organizing")
    assert s.find_running()["id"] == a["id"]
    assert s.next_queued()["id"] == b["id"]
    s.update(a["id"], status="awaiting_confirm")
    assert s.find_running() is None           # 挂起闸门不占 worker


def test_cancelled_queued_not_picked(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    s.update(a["id"], cancel_requested=True)
    assert s.next_queued() is None


def test_mark_interrupted(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    s.update(a["id"], status="writing")
    n = s.mark_interrupted()
    assert n == 1
    j = s.get(a["id"])
    assert j["status"] == "failed" and j["interrupted"] is True
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_jobs.py -q`
Expected: ImportError（quickstudy.web 不存在）

- [ ] **Step 3: 实现 jobs.py**

`quickstudy/web/__init__.py` 为空文件。`quickstudy/web/jobs.py`：

```python
"""Web 任务注册表：jobs.json 持久化 + 状态机常量（design §3）。"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

STAGES = ["crawling", "organizing", "demoing", "outlining", "writing"]
RUNNING_STATES = {"queued", *STAGES}       # 占用 worker
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
        return next((j for j in self._load() if j["status"] in RUNNING_STATES), None)

    def next_queued(self) -> dict | None:
        qs = [j for j in self._load()
              if j["status"] == "queued" and not j["cancel_requested"]]
        return min(qs, key=lambda j: j["created_at"]) if qs else None

    def mark_interrupted(self) -> int:
        """服务启动时调用：上次崩溃遗留的运行态任务标 failed+interrupted。"""
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_jobs.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add quickstudy/web/__init__.py quickstudy/web/jobs.py tests/test_web_jobs.py
git commit -m "feat(web): job store with state machine and jobs.json persistence"
```

---

### Task 2: derive_progress 产物进度推导

**Files:**
- Modify: `quickstudy/web/jobs.py`（类内追加方法 + 模块级 `_read_json` 辅助函数）
- Test: `tests/test_web_jobs.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `JobStore.task_dir`
- Produces: `JobStore.derive_progress(job: dict) -> dict`，返回：
  `{"stage": str, "detail": {"crawl"?: {"discovered": int, "parsed": int}, "organize"?: {"concepts": int, "edges": int}, "glossary_terms"?: int, "demos"?: {"done": int, "passed": int}, "outline"?: {"book_title": str, "chapters": int}, "writing"?: {"written": int, "total": int}}, "recent_log": list[str], "cost": {"tokens_in": int, "tokens_out": int}}`
  （键只在对应产物存在时出现；api.py 与前端 JobDetail 依赖此结构）

- [ ] **Step 1: 写失败测试（追加到 tests/test_web_jobs.py）**

```python
import json


def _make_artifacts(ws_root):
    """构造最小产物集：manifest + graph + glossary + outline + chapters/state + llm_cost + job.log"""
    d = ws_root / "fastapi-tiangolo-com"
    (d / "chapters").mkdir(parents=True)
    (d / "demos" / "p1" / "g1").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"pages": {
        "https://x/a": {"parsed": True}, "https://x/b": {"parsed": False}}}))
    (d / "graph.json").write_text(json.dumps(
        {"concepts": [{"name": "c1"}], "concept_edges": [{"from": 0, "to": 0}]}))
    (d / "glossary.json").write_text(json.dumps({"n_terms": 7, "terms": {}}))
    (d / "outline.json").write_text(json.dumps(
        {"book_title": "测试手册", "chapters": [{"no": 1}, {"no": 2}]}))
    (d / "chapters" / "state.json").write_text(json.dumps({"written": {"1": {}}}))
    (d / "demos" / "p1" / "g1" / "exec_report.json").write_text(
        json.dumps({"status": "passed"}))
    (d / "llm_cost_m4_book.json").write_text(json.dumps(
        {"total_tokens": {"in": 100, "out": 50}}))
    (d / "job.log").write_text("\n".join(f"line{i}" for i in range(30)))


def test_derive_progress(tmp_path):
    _make_artifacts(tmp_path)
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com")
    p = s.derive_progress(j)
    assert p["detail"]["crawl"] == {"discovered": 2, "parsed": 1}
    assert p["detail"]["organize"] == {"concepts": 1, "edges": 1}
    assert p["detail"]["glossary_terms"] == 7
    assert p["detail"]["demos"] == {"done": 1, "passed": 1}
    assert p["detail"]["outline"] == {"book_title": "测试手册", "chapters": 2}
    assert p["detail"]["writing"] == {"written": 1, "total": 2}
    assert p["cost"] == {"tokens_in": 100, "tokens_out": 50}
    assert len(p["recent_log"]) == 20 and p["recent_log"][-1] == "line29"


def test_derive_progress_empty(tmp_path):
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com")
    p = s.derive_progress(j)
    assert p["detail"] == {} and p["cost"] == {"tokens_in": 0, "tokens_out": 0}
    assert p["recent_log"] == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_jobs.py -q`
Expected: AttributeError: no attribute derive_progress

- [ ] **Step 3: 实现（jobs.py 末尾追加模块函数 + JobStore 内追加方法）**

模块级（文件末尾）：

```python
def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
```

JobStore 内：

```python
    def derive_progress(self, job: dict) -> dict:
        """从产物推导进度与成本（不解析日志；断点续跑后天然正确）。"""
        d = self.task_dir(job)
        detail: dict = {}
        manifest = _read_json(d / "manifest.json")
        if manifest:
            pages = manifest.get("pages", {})
            detail["crawl"] = {"discovered": len(pages),
                               "parsed": sum(1 for e in pages.values() if e.get("parsed"))}
        graph = _read_json(d / "graph.json")
        if graph and graph.get("concepts"):
            detail["organize"] = {"concepts": len(graph["concepts"]),
                                  "edges": len(graph.get("concept_edges", []))}
        glossary = _read_json(d / "glossary.json")
        if glossary:
            detail["glossary_terms"] = glossary.get("n_terms", 0)
        demo_reports = list(d.glob("demos/*/*/exec_report.json"))
        if demo_reports:
            passed = sum(1 for r in demo_reports
                         if (_read_json(r) or {}).get("status") == "passed")
            detail["demos"] = {"done": len(demo_reports), "passed": passed}
        outline = _read_json(d / "outline.json")
        if outline:
            detail["outline"] = {"book_title": outline.get("book_title", ""),
                                 "chapters": len(outline.get("chapters", []))}
        state = _read_json(d / "chapters" / "state.json")
        if state and outline:
            detail["writing"] = {"written": len(state.get("written", {})),
                                 "total": len(outline.get("chapters", []))}
        recent_log: list[str] = []
        log_file = d / "job.log"
        if log_file.exists():
            recent_log = log_file.read_text(
                encoding="utf-8", errors="replace").splitlines()[-20:]
        tin = tout = 0
        for cf in d.glob("llm_cost*.json"):
            t = (_read_json(cf) or {}).get("total_tokens", {})
            tin += t.get("in", 0)
            tout += t.get("out", 0)
        return {"stage": job["status"], "detail": detail, "recent_log": recent_log,
                "cost": {"tokens_in": tin, "tokens_out": tout}}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_jobs.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add quickstudy/web/jobs.py tests/test_web_jobs.py
git commit -m "feat(web): artifact-derived progress and cost aggregation"
```

---

### Task 3: Runner 串行执行器

**Files:**
- Create: `quickstudy/web/runner.py`
- Test: `tests/test_web_runner.py`

**Interfaces:**
- Consumes: `JobStore`（Task 1/2）；`quickstudy.config.TaskConfig.load(url, workspace=str, max_pages=int|None)`
- Produces:
  - `StageFn = Callable[[TaskConfig], Awaitable[object]]`
  - `default_stage_fns() -> dict[str, StageFn]`（键 = STAGES 五段；lazy import pipeline 函数）
  - `Runner(store: JobStore, stage_fns: dict | None = None, docker_check: Callable[[], bool] | None = None)`
    - `async run_next() -> dict | None`（worker 空闲且有 queued 任务时跑一个，跑到 awaiting_confirm 或终态）
    - `async confirm(job_id: str) -> dict`（awaiting_confirm → writing → done/failed；状态不符抛 ValueError，worker 忙抛 RuntimeError，不存在抛 KeyError）
    - `request_cancel(job_id: str)`（awaiting_confirm 直接 cancelled；否则置 cancel_requested，阶段间生效）
  - 运行期间 logging 落盘 `workspace/{task_id}/job.log`（FileHandler 挂在 `quickstudy` logger）

- [ ] **Step 1: 写失败测试**

```python
"""Runner 执行器单测：mock 阶段函数，不触网不触 Docker。"""
import pytest
from quickstudy.web.jobs import JobStore
from quickstudy.web.runner import Runner


def _recorder(calls, fail_at=None, cancel_at=None, store=None):
    async def make(stage):
        async def fn(cfg):
            calls.append(stage)
            if fail_at == stage:
                raise RuntimeError("boom")
            if cancel_at == stage and store:
                # 模拟运行中收到取消请求
                job = store.list()[0]
                store.update(job["id"], cancel_requested=True)
        return fn
    return make


def _runner(tmp_path, calls, **kw):
    store = JobStore(tmp_path)
    fns = {s: _recorder(calls, **{k: v for k, v in kw.items()
                                  if k in ("fail_at", "cancel_at")},
                        store=kw.get("store"))(s)
           for s in ["crawling", "organizing", "demoing", "outlining", "writing"]}
    return store, Runner(store, stage_fns=fns, docker_check=lambda: kw.get("docker", True))


async def test_pre_gate_to_awaiting_confirm(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    job = store.create("https://a.com")
    await r.run_next()
    assert calls == ["crawling", "organizing", "demoing", "outlining"]
    assert store.get(job["id"])["status"] == "awaiting_confirm"


async def test_confirm_runs_writing_to_done(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    job = store.create("https://a.com")
    await r.run_next()
    await r.confirm(job["id"])
    assert calls[-1] == "writing"
    assert store.get(job["id"])["status"] == "done"


async def test_confirm_wrong_status_raises(tmp_path):
    store, r = _runner(tmp_path, [])
    job = store.create("https://a.com")
    with pytest.raises(ValueError):
        await r.confirm(job["id"])


async def test_stage_failure_marks_failed(tmp_path):
    store, r = _runner(tmp_path, [], fail_at="organizing")
    job = store.create("https://a.com")
    await r.run_next()
    j = store.get(job["id"])
    assert j["status"] == "failed" and "boom" in j["error"]


async def test_docker_unavailable_skips_demos(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls, docker=False)
    job = store.create("https://a.com")
    await r.run_next()
    assert "demoing" not in calls
    assert store.get(job["id"])["skipped_demos"] is True


async def test_with_demos_false_skips(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    store.create("https://a.com", with_demos=False)
    await r.run_next()
    assert "demoing" not in calls


async def test_cancel_between_stages(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls, cancel_at="organizing", store=None)
    job = store.create("https://a.com")
    # cancel_at 需要 store 引用，改为直接在阶段间置位：
    # 用 request_cancel 在 awaiting_confirm 上立即生效
    await r.run_next()
    r.request_cancel(job["id"])
    assert store.get(job["id"])["status"] == "cancelled"


async def test_serial_single_job(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    a = store.create("https://a.com")
    b = store.create("https://b.com")
    await r.run_next()   # a 停在闸门
    await r.run_next()   # worker 空闲（闸门挂起不占），b 开跑
    assert store.get(a["id"])["status"] == "awaiting_confirm"
    assert store.get(b["id"])["status"] == "awaiting_confirm"
```

注：`cancel_at`/`store` 组合在 `_recorder` 中保留（用于演示运行中取消的路径由 `_run_pre_gate` 的阶段间检查处理——本计划用 awaiting_confirm 立即取消覆盖，运行中取消走同一 `cancel_requested` 标志位逻辑，不再单测模拟并发时序）。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_runner.py -q`
Expected: ImportError

- [ ] **Step 3: 实现 runner.py**

```python
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
        h = logging.FileHandler(self.store.task_dir(job) / "job.log",
                                encoding="utf-8")
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_runner.py -q`
Expected: 8 passed（pytest-asyncio auto 模式已配置）

- [ ] **Step 5: Commit**

```bash
git add quickstudy/web/runner.py tests/test_web_runner.py
git commit -m "feat(web): serial pipeline runner with cost gate and cancel"
```

---

### Task 4: FastAPI 路由层

**Files:**
- Create: `quickstudy/web/api.py`
- Test: `tests/test_web_api.py`
- Modify: `pyproject.toml`（optional-dependencies 加 `web = ["fastapi>=0.110", "uvicorn>=0.29"]`）

**Interfaces:**
- Consumes: `JobStore`、`Runner`（duck-typed，测试用 FakeRunner）
- Produces: `create_app(workspace_root: Path | str = "workspace", static_dir: Path | str | None = None, runner: Runner | None = None) -> FastAPI`；`app.state.store` / `app.state.runner`
- 端点（全部 JSON；错误 `{"detail": str}`）：
  - `POST /api/jobs` 201 `{url, with_demos=true, max_pages=null}`；URL 非法或 LLM env 缺失 → 400
  - `GET /api/jobs` / `GET /api/jobs/{id}`（详情含 `progress` = derive_progress 结果）；不存在 → 404
  - `GET /api/jobs/{id}/outline` → outline.json 内容；无 → 404
  - `POST /api/jobs/{id}/confirm`：状态非 awaiting_confirm → 409；否则后台 task 触发 `runner.confirm`
  - `POST /api/jobs/{id}/cancel` → request_cancel 结果
  - `GET /api/jobs/{id}/book` → `{"book_title", "chapters": [{"no","title","filename"|null}], "glossary": {...}|null}`
  - `GET /api/jobs/{id}/chapters/{filename}` → `{"filename", "markdown"}`；文件不存在 → 404
  - static_dir 存在时 `app.mount("/", StaticFiles(html=True))`

- [ ] **Step 1: 装依赖 + 写失败测试**

Run: `.venv\Scripts\python.exe -m pip install "fastapi>=0.110" "uvicorn>=0.29"`

pyproject.toml 的 `[project.optional-dependencies]` 追加：

```toml
web = [
    "fastapi>=0.110",
    "uvicorn>=0.29",
]
```

```python
"""Web API 契约测试：ASGI 直连，runner 用假实现，不触网。"""
import json

import pytest
from httpx import ASGITransport, AsyncClient

from quickstudy.web.api import create_app


class FakeRunner:
    def __init__(self):
        self.started = []
        self.confirmed = []
        self.cancelled = []

    async def run_next(self):
        return None

    async def confirm(self, job_id):
        self.confirmed.append(job_id)

    def request_cancel(self, job_id):
        self.cancelled.append(job_id)
        return {"id": job_id, "status": "cancelled"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://x")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "y")
    app = create_app(workspace_root=tmp_path, runner=FakeRunner())
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_create_validation(client, monkeypatch):
    async with client as c:
        r = await c.post("/api/jobs", json={"url": "notaurl"})
        assert r.status_code == 400
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN")
    async with client as c:
        r = await c.post("/api/jobs", json={"url": "https://a.com"})
        assert r.status_code == 400


async def test_create_and_get(client):
    async with client as c:
        r = await c.post("/api/jobs", json={"url": "https://a.com",
                                            "with_demos": False})
        assert r.status_code == 201
        job = r.json()
        assert job["status"] == "queued"
        r = await c.get(f"/api/jobs/{job['id']}")
        assert r.status_code == 200 and "progress" in r.json()
        r = await c.get("/api/jobs")
        assert len(r.json()) == 1
        r = await c.get("/api/jobs/nope")
        assert r.status_code == 404


async def test_confirm_gate(client):
    async with client as c:
        job = (await c.post("/api/jobs", json={"url": "https://a.com"})).json()
        r = await c.post(f"/api/jobs/{job['id']}/confirm")
        assert r.status_code == 409                      # queued 状态不能确认
        app = c._transport.app
        app.state.store.update(job["id"], status="awaiting_confirm")
        r = await c.post(f"/api/jobs/{job['id']}/confirm")
        assert r.status_code == 200
        await app.state.runner.confirmed and None        # FakeRunner 记录
        assert app.state.runner.confirmed == [job["id"]]


async def test_cancel(client):
    async with client as c:
        job = (await c.post("/api/jobs", json={"url": "https://a.com"})).json()
        r = await c.post(f"/api/jobs/{job['id']}/cancel")
        assert r.status_code == 200


async def test_book_and_chapter(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://x")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "y")
    d = tmp_path / "a-com"
    (d / "chapters").mkdir(parents=True)
    (d / "outline.json").write_text(json.dumps(
        {"book_title": "书", "chapters": [{"no": 1, "title": "入门"}]}))
    (d / "chapters" / "state.json").write_text(json.dumps(
        {"written": {"1": {"no": 1, "filename": "01-入门.md"}}}))
    (d / "chapters" / "01-入门.md").write_text("# 第1章")
    (d / "glossary.json").write_text(json.dumps({"terms": {"FastAPI": {}}}))
    app = create_app(workspace_root=tmp_path, runner=FakeRunner())
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        job = (await c.post("/api/jobs", json={"url": "https://a.com"})).json()
        r = await c.get(f"/api/jobs/{job['id']}/book")
        assert r.json()["chapters"][0]["filename"] == "01-入门.md"
        assert r.json()["glossary"]["terms"]
        r = await c.get(f"/api/jobs/{job['id']}/chapters/01-入门.md")
        assert r.json()["markdown"] == "# 第1章"
        r = await c.get(f"/api/jobs/{job['id']}/chapters/nope.md")
        assert r.status_code == 404
```

注：`url_to_task_id("https://a.com")` 产出 `a-com`，fixture 目录名与之对应。

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_api.py -q`
Expected: ImportError

- [ ] **Step 3: 实现 api.py**

```python
"""FastAPI 路由薄层（design §4）：只做校验/序列化，业务在 jobs.py / runner.py。"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from quickstudy.web.jobs import JobStore, _read_json
from quickstudy.web.runner import Runner


class NewJob(BaseModel):
    url: str
    with_demos: bool = True
    max_pages: int | None = None


def _get_job(store: JobStore, job_id: str) -> dict:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(404, "任务不存在")
    return job


def create_app(workspace_root: Path | str = "workspace",
               static_dir: Path | str | None = None,
               runner: Runner | None = None) -> FastAPI:
    store = JobStore(workspace_root)
    store.mark_interrupted()
    runner = runner or Runner(store)
    app = FastAPI(title="quickstudy")
    app.state.store = store
    app.state.runner = runner

    @app.post("/api/jobs", status_code=201)
    async def create_job(body: NewJob):
        u = urlparse(body.url)
        if u.scheme not in ("http", "https") or not u.netloc:
            raise HTTPException(400, "url 必须是完整 http(s) 地址")
        if not (os.environ.get("ANTHROPIC_BASE_URL")
                and os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            raise HTTPException(400, "LLM 网关未配置（ANTHROPIC_BASE_URL/AUTH_TOKEN）")
        job = store.create(body.url, body.with_demos, body.max_pages)
        asyncio.create_task(runner.run_next())
        return job

    @app.get("/api/jobs")
    async def list_jobs():
        return store.list()

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        job = _get_job(store, job_id)
        return {**job, "progress": store.derive_progress(job)}

    @app.get("/api/jobs/{job_id}/outline")
    async def get_outline(job_id: str):
        job = _get_job(store, job_id)
        outline = _read_json(store.task_dir(job) / "outline.json")
        if outline is None:
            raise HTTPException(404, "大纲尚未生成")
        return outline

    @app.post("/api/jobs/{job_id}/confirm")
    async def confirm(job_id: str):
        job = _get_job(store, job_id)
        if job["status"] != "awaiting_confirm":
            raise HTTPException(409, f"任务状态 {job['status']} 不能确认")
        asyncio.create_task(runner.confirm(job_id))
        return store.get(job_id)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel(job_id: str):
        _get_job(store, job_id)
        try:
            return runner.request_cancel(job_id)
        except KeyError:
            raise HTTPException(404, "任务不存在") from None

    @app.get("/api/jobs/{job_id}/book")
    async def get_book(job_id: str):
        job = _get_job(store, job_id)
        d = store.task_dir(job)
        outline = _read_json(d / "outline.json")
        if outline is None:
            raise HTTPException(404, "大纲尚未生成")
        state = _read_json(d / "chapters" / "state.json") or {"written": {}}
        by_no = {m["no"]: m["filename"] for m in state["written"].values()}
        chapters = [{"no": ch["no"], "title": ch["title"],
                     "filename": by_no.get(ch["no"])}
                    for ch in outline.get("chapters", [])]
        return {"book_title": outline.get("book_title", ""),
                "chapters": chapters,
                "glossary": _read_json(d / "glossary.json")}

    @app.get("/api/jobs/{job_id}/chapters/{filename}")
    async def get_chapter(job_id: str, filename: str):
        job = _get_job(store, job_id)
        if "/" in filename or ".." in filename:
            raise HTTPException(400, "非法文件名")
        f = store.task_dir(job) / "chapters" / filename
        if not f.exists():
            raise HTTPException(404, "章节不存在")
        return {"filename": filename,
                "markdown": f.read_text(encoding="utf-8")}

    if static_dir and Path(static_dir).exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True),
                  name="web")
    return app
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web_api.py -q`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add quickstudy/web/api.py tests/test_web_api.py pyproject.toml
git commit -m "feat(web): FastAPI routes - jobs CRUD, gate confirm, book reader API"
```

---

### Task 5: CLI serve 子命令

**Files:**
- Modify: `quickstudy/cli.py`（parser + 分支）
- Test: `tests/test_web_api.py`（追加 1 个冒烟测试）

**Interfaces:**
- Consumes: `create_app`
- Produces: `quickstudy serve [--host 127.0.0.1] [--port 8600] [--workspace workspace] [--web-dist web/dist]`

- [ ] **Step 1: 写失败测试（追加）**

```python
def test_serve_parser():
    from quickstudy.cli import build_parser
    args = build_parser().parse_args(["serve", "--port", "9900"])
    assert args.command == "serve" and args.port == 9900
    assert args.host == "127.0.0.1"
```

- [ ] **Step 2: 跑测试确认失败**（SystemExit / argparse error）

- [ ] **Step 3: 实现（cli.py 两处修改）**

parser 内追加（`return p` 之前）：

```python
    serve = sub.add_parser("serve", help="Web 服务：前端界面 + 任务 API（本地自用）")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8600)
    serve.add_argument("--workspace", default="workspace")
    serve.add_argument("--web-dist", default="web/dist", help="前端 build 产物目录")
```

main() 内 `return 1` 之前追加：

```python
    if args.command == "serve":
        try:
            import uvicorn
        except ImportError:
            print("缺少依赖：pip install -e \".[web]\"")
            return 1
        from quickstudy.web.api import create_app

        app = create_app(workspace_root=args.workspace, static_dir=args.web_dist)
        print(f"quickstudy Web 已启动: http://{args.host}:{args.port}")
        uvicorn.run(app, host=args.host, port=args.port)
        return 0
```

- [ ] **Step 4: 跑测试确认通过 + 全量回归**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`
Expected: 全部通过（66+）

- [ ] **Step 5: Commit**

```bash
git add quickstudy/cli.py tests/test_web_api.py
git commit -m "feat(web): serve subcommand with static hosting"
```

---

### Task 6: 前端工程脚手架

**Files:**
- Create: `web/package.json`、`web/vite.config.js`、`web/index.html`、`web/src/main.js`、`web/src/App.vue`、`web/src/router.js`、`web/src/api.js`
- Modify: `.gitignore`（追加 `web/node_modules/`、`web/dist/`）

**Interfaces:**
- Produces（Task 7-9 依赖）:
  - `api` 对象（src/api.js）：`listJobs() createJob(url, withDemos) getJob(id) getOutline(id) confirm(id) cancel(id) getBook(id) getChapter(id, filename)`——全部返回 Promise<JSON>
  - 路由：`/` → Home、`/job/:id` → JobDetail、`/job/:id/read` → Reader（hash 模式）

- [ ] **Step 1: 写全部脚手架文件**

`web/package.json`：

```json
{
  "name": "quickstudy-web",
  "private": true,
  "version": "0.1.0",
  "scripts": { "dev": "vite", "build": "vite build" },
  "dependencies": {
    "element-plus": "^2.9.1",
    "highlight.js": "^11.10.0",
    "markdown-it": "^14.1.0",
    "vue": "^3.5.13",
    "vue-router": "^4.5.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.2.1",
    "vite": "^6.0.7"
  }
}
```

`web/vite.config.js`：

```js
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { proxy: { '/api': 'http://127.0.0.1:8600' } },
})
```

`web/index.html`：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>quickstudy 学习手册生成</title></head>
<body><div id="app"></div><script type="module" src="/src/main.js"></script></body>
</html>
```

`web/src/main.js`：

```js
import { createApp } from 'vue'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'
import App from './App.vue'
import { router } from './router'

createApp(App).use(router).use(ElementPlus, { locale: zhCn }).mount('#app')
```

`web/src/router.js`：

```js
import { createRouter, createWebHashHistory } from 'vue-router'
import Home from './views/Home.vue'
import JobDetail from './views/JobDetail.vue'
import Reader from './views/Reader.vue'

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/job/:id', component: JobDetail },
    { path: '/job/:id/read', component: Reader },
  ],
})
```

`web/src/api.js`：

```js
const base = '/api'

async function req(path, opts = {}) {
  const r = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' }, ...opts,
  })
  if (!r.ok) {
    const body = await r.json().catch(() => ({}))
    throw new Error(body.detail || `${r.status} ${r.statusText}`)
  }
  return r.json()
}

export const api = {
  listJobs: () => req('/jobs'),
  createJob: (url, withDemos) =>
    req('/jobs', { method: 'POST', body: JSON.stringify({ url, with_demos: withDemos }) }),
  getJob: (id) => req(`/jobs/${id}`),
  getOutline: (id) => req(`/jobs/${id}/outline`),
  confirm: (id) => req(`/jobs/${id}/confirm`, { method: 'POST' }),
  cancel: (id) => req(`/jobs/${id}/cancel`, { method: 'POST' }),
  getBook: (id) => req(`/jobs/${id}/book`),
  getChapter: (id, filename) =>
    req(`/jobs/${id}/chapters/${encodeURIComponent(filename)}`),
}
```

`web/src/App.vue`：

```vue
<template>
  <el-config-provider>
    <div class="page">
      <header class="topbar" @click="$router.push('/')">
        <h2>quickstudy · 文档站 → 中文学习手册</h2>
      </header>
      <router-view />
    </div>
  </el-config-provider>
</template>

<style>
body { margin: 0; background: #f5f7fa; }
.page { max-width: 1080px; margin: 0 auto; padding: 0 16px 40px; }
.topbar { cursor: pointer; padding: 12px 0; }
.topbar h2 { margin: 0; color: #303133; }
</style>
```

`.gitignore` 追加：

```
web/node_modules/
web/dist/
```

- [ ] **Step 2: 安装依赖并验证构建**

Run: `cd web; npm install; npm run build`
Expected: `dist/` 产出 index.html + assets（首次 install 约 1-2 分钟）

- [ ] **Step 3: Commit**

```bash
git add web/ .gitignore
git commit -m "feat(web): Vue3 + Element Plus scaffold with Vite"
```

---

### Task 7: Home 首页（新建任务 + 历史列表）

**Files:**
- Create: `web/src/views/Home.vue`

**Interfaces:**
- Consumes: `api.listJobs / createJob`；job 字段同 Task 1
- Produces: 无新接口（页面组件）

- [ ] **Step 1: 实现 Home.vue**

```vue
<template>
  <div>
    <el-card class="new-job">
      <el-form @submit.prevent="submit">
        <el-input v-model="url" size="large" :disabled="hasActive"
                  placeholder="粘贴官方文档站地址，如 https://fastapi.tiangolo.com">
          <template #append>
            <el-checkbox v-model="withDemos" :disabled="hasActive">生成 Demo</el-checkbox>
          </template>
        </el-input>
        <el-button class="submit" type="primary" size="large" :loading="submitting"
                   :disabled="hasActive || !url.trim()" @click="submit">
          {{ hasActive ? '有任务正在进行（单任务串行）' : '开始生成' }}
        </el-button>
        <el-alert v-if="error" :title="error" type="error" show-icon class="error" />
      </el-form>
    </el-card>

    <h3>历史任务</h3>
    <el-empty v-if="!jobs.length" description="还没有任务" />
    <el-card v-for="j in jobs" :key="j.id" class="job-card" shadow="hover"
             @click="open(j)">
      <div class="row">
        <span class="url">{{ j.url }}</span>
        <el-tag :type="tagType(j.status)">{{ statusText(j) }}</el-tag>
      </div>
      <div class="meta">创建于 {{ fmt(j.created_at) }}<span v-if="j.error">｜{{ j.error }}</span></div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { api } from '../api'

const router = useRouter()
const url = ref('')
const withDemos = ref(true)
const jobs = ref([])
const submitting = ref(false)
const error = ref('')

const ACTIVE = ['queued', 'crawling', 'organizing', 'demoing', 'outlining', 'writing']
const hasActive = computed(() => jobs.value.some(j => ACTIVE.includes(j.status)))

const STATUS_TEXT = {
  queued: '排队中', crawling: '爬取中', organizing: '知识组织', demoing: 'Demo 重构',
  outlining: '生成大纲', awaiting_confirm: '待确认写书', writing: '写作中',
  done: '已完成', failed: '失败', cancelled: '已取消',
}
const statusText = (j) => STATUS_TEXT[j.status] || j.status
const tagType = (s) => (s === 'done' ? 'success'
  : s === 'failed' ? 'danger' : s === 'awaiting_confirm' ? 'warning' : 'info')
const fmt = (ts) => new Date(ts * 1000).toLocaleString('zh-CN')

async function load() { jobs.value = await api.listJobs() }

async function submit() {
  submitting.value = true
  error.value = ''
  try {
    const job = await api.createJob(url.value.trim(), withDemos.value)
    router.push(`/job/${job.id}`)
  } catch (e) {
    error.value = e.message
  } finally {
    submitting.value = false
  }
}

function open(j) {
  router.push(j.status === 'done' ? `/job/${j.id}/read` : `/job/${j.id}`)
}

onMounted(load)
</script>

<style scoped>
.new-job { margin-bottom: 16px; }
.submit { margin-top: 12px; width: 100%; }
.error { margin-top: 12px; }
.job-card { margin-bottom: 10px; cursor: pointer; }
.row { display: flex; justify-content: space-between; align-items: center; }
.url { font-weight: 600; }
.meta { color: #909399; font-size: 13px; margin-top: 6px; }
</style>
```

- [ ] **Step 2: 手动验收**

Run: 后端 `.venv\Scripts\quickstudy.exe serve` + 前端 `cd web; npm run dev`
预期：打开 vite 提示的地址，输入非法 URL 点生成显示错误；历史任务列表展示 workspace/jobs.json 内容

- [ ] **Step 3: Commit**

```bash
git add web/src/views/Home.vue
git commit -m "feat(web): home page - new job form and history list"
```

---

### Task 8: JobDetail 任务详情页（步骤条 + 进度 + 闸门）

**Files:**
- Create: `web/src/views/JobDetail.vue`

**Interfaces:**
- Consumes: `api.getJob / getOutline / confirm / cancel`；progress 结构同 Task 2
- Produces: 无新接口

- [ ] **Step 1: 实现 JobDetail.vue**

```vue
<template>
  <div v-if="job">
    <el-card>
      <template #header>
        <div class="row">
          <span class="url">{{ job.url }}</span>
          <el-tag :type="job.status === 'failed' ? 'danger' : 'info'">
            {{ statusText[job.status] || job.status }}
          </el-tag>
        </div>
      </template>

      <el-steps :active="activeStep" align-center finish-status="success">
        <el-step title="爬取解析" />
        <el-step title="知识组织" />
        <el-step :title="job.skipped_demos ? 'Demo（已跳过）' : 'Demo 重构'" />
        <el-step title="生成大纲" />
        <el-step title="分章写作" />
      </el-steps>

      <el-descriptions :column="3" border class="progress">
        <el-descriptions-item v-if="d.crawl" label="页面">
          解析 {{ d.crawl.parsed }}/{{ d.crawl.discovered }}
        </el-descriptions-item>
        <el-descriptions-item v-if="d.organize" label="概念/边">
          {{ d.organize.concepts }}/{{ d.organize.edges }}
        </el-descriptions-item>
        <el-descriptions-item v-if="d.demos" label="Demo">
          {{ d.demos.passed }}/{{ d.demos.done }} 通过
        </el-descriptions-item>
        <el-descriptions-item v-if="d.writing" label="章节">
          {{ d.writing.written }}/{{ d.writing.total }}
        </el-descriptions-item>
        <el-descriptions-item label="token 消耗">
          {{ fmtK(cost.tokens_in) }} 入 / {{ fmtK(cost.tokens_out) }} 出
        </el-descriptions-item>
      </el-descriptions>

      <el-alert v-if="job.error" :title="job.error" type="error" show-icon class="block" />

      <div v-if="job.status === 'awaiting_confirm'" class="gate block">
        <el-alert type="warning" show-icon :closable="false"
                  title="大纲已生成，确认后才消耗写作 token" />
        <h3 v-if="outline">《{{ outline.book_title }}》</h3>
        <el-tree v-if="outline" :data="outlineTree" default-expand-all class="tree" />
        <el-button type="primary" size="large" :loading="acting" @click="doConfirm">
          确认写书
        </el-button>
        <el-button size="large" :loading="acting" @click="doCancel">放弃</el-button>
      </div>

      <div v-if="job.status === 'done'" class="block">
        <el-button type="success" size="large"
                   @click="$router.push(`/job/${job.id}/read`)">开始阅读</el-button>
      </div>
      <div v-if="isActive" class="block">
        <el-button type="danger" plain @click="doCancel">取消任务</el-button>
      </div>
    </el-card>

    <el-card v-if="recentLog.length" class="block">
      <template #header>最近日志</template>
      <pre class="log">{{ recentLog.join('\n') }}</pre>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api } from '../api'

const route = useRoute()
const router = useRouter()
const id = route.params.id
const job = ref(null)
const outline = ref(null)
const acting = ref(false)
let timer = null

const STATUS_TEXT = {
  queued: '排队中', crawling: '爬取中', organizing: '知识组织', demoing: 'Demo 重构',
  outlining: '生成大纲', awaiting_confirm: '待确认写书', writing: '写作中',
  done: '已完成', failed: '失败', cancelled: '已取消',
}
const STEP_OF = { queued: 0, crawling: 0, organizing: 1, demoing: 2, outlining: 3,
  awaiting_confirm: 4, writing: 4, done: 5, failed: 0, cancelled: 0 }

const d = computed(() => job.value?.progress?.detail || {})
const cost = computed(() => job.value?.progress?.cost || { tokens_in: 0, tokens_out: 0 })
const recentLog = computed(() => job.value?.progress?.recent_log || [])
const activeStep = computed(() => STEP_OF[job.value?.status] ?? 0)
const isActive = computed(() =>
  ['queued', 'crawling', 'organizing', 'demoing', 'outlining', 'writing']
    .includes(job.value?.status))
const outlineTree = computed(() => (outline.value?.chapters || []).map((c) => ({
  label: `第${c.no}章 ${c.title}（${'★'.repeat(c.difficulty)} ~${c.est_hours}h）`,
  children: [{ label: c.summary }],
})))
const fmtK = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : `${n}`)

async function refresh() {
  job.value = await api.getJob(id)
  if (job.value.status === 'awaiting_confirm' && !outline.value) {
    outline.value = await api.getOutline(id)
  }
  if (!isActive.value && job.value.status !== 'awaiting_confirm' && timer) {
    clearInterval(timer)
    timer = null
  }
}

async function doConfirm() {
  acting.value = true
  try { await api.confirm(id); await refresh() } finally { acting.value = false }
}

async function doCancel() {
  acting.value = true
  try { await api.cancel(id); await refresh(); router.push('/') }
  finally { acting.value = false }
}

onMounted(async () => {
  await refresh()
  timer = setInterval(refresh, 2000)
})
onUnmounted(() => timer && clearInterval(timer))
</script>

<style scoped>
.row { display: flex; justify-content: space-between; align-items: center; }
.url { font-weight: 600; }
.progress { margin-top: 16px; }
.block { margin-top: 16px; }
.tree { margin: 12px 0; }
.log { max-height: 300px; overflow: auto; font-size: 12px; margin: 0; }
</style>
```

- [ ] **Step 2: 手动验收**

预期：用既有 FastAPI workspace 起服务；任务详情页步骤条/进度/成本正确；awaiting_confirm 任务显示大纲树与确认/放弃按钮；done 任务显示"开始阅读"

- [ ] **Step 3: Commit**

```bash
git add web/src/views/JobDetail.vue
git commit -m "feat(web): job detail page with steps, progress, cost gate"
```

---

### Task 9: Reader 阅读页

**Files:**
- Create: `web/src/views/Reader.vue`

**Interfaces:**
- Consumes: `api.getBook / getChapter`
- Produces: 无新接口

- [ ] **Step 1: 实现 Reader.vue**

```vue
<template>
  <div class="reader">
    <aside class="side">
      <h3 class="book-title">{{ book?.book_title || '加载中…' }}</h3>
      <el-menu :default-active="current" @select="select">
        <el-menu-item v-for="c in chapters" :key="c.no" :index="c.filename || ''"
                      :disabled="!c.filename">
          第{{ c.no }}章 {{ c.title }}
        </el-menu-item>
        <el-menu-item v-if="glossaryTerms.length" index="__glossary">
          附录：术语表
        </el-menu-item>
      </el-menu>
    </aside>
    <main class="content">
      <div v-if="current === '__glossary'">
        <h1>术语表</h1>
        <el-table :data="glossaryTerms" size="small">
          <el-table-column prop="term" label="英文" width="220" />
          <el-table-column prop="zh" label="推荐译名" width="200" />
          <el-table-column prop="note" label="说明" />
        </el-table>
      </div>
      <article v-else class="markdown" v-html="html" />
    </main>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import MarkdownIt from 'markdown-it'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import { api } from '../api'

const route = useRoute()
const id = route.params.id
const book = ref(null)
const current = ref('')
const markdown = ref('')

const md = new MarkdownIt({
  html: true, linkify: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return `<pre><code class="hljs">${hljs.highlight(code, { language: lang }).value}</code></pre>`
    }
    return `<pre><code class="hljs">${md.utils.escapeHtml(code)}</code></pre>`
  },
})

const chapters = computed(() => book.value?.chapters || [])
const glossaryTerms = computed(() =>
  Object.entries(book.value?.glossary?.terms || {})
    .map(([term, e]) => ({ term, zh: e.keep_english ? '（保留英文）' : e.translation,
                           note: e.note || '' }))
    .sort((a, b) => a.term.localeCompare(b.term)))
const html = computed(() => md.render(markdown.value))

async function select(filename) {
  if (!filename) return
  current.value = filename
  if (filename === '__glossary') return
  const r = await api.getChapter(id, filename)
  markdown.value = r.markdown
}

onMounted(async () => {
  book.value = await api.getBook(id)
  const first = chapters.value.find((c) => c.filename)
  if (first) await select(first.filename)
})
</script>

<style scoped>
.reader { display: flex; gap: 20px; align-items: flex-start; }
.side { width: 300px; flex-shrink: 0; background: #fff; border-radius: 8px;
        position: sticky; top: 16px; max-height: 85vh; overflow: auto; }
.book-title { padding: 12px 16px 0; font-size: 15px; }
.content { flex: 1; background: #fff; border-radius: 8px; padding: 24px 32px; }
.markdown :deep(pre) { background: #f6f8fa; padding: 12px; border-radius: 6px;
                       overflow: auto; }
.markdown :deep(table) { border-collapse: collapse; }
.markdown :deep(th), .markdown :deep(td) { border: 1px solid #dcdfe6;
                                           padding: 6px 12px; }
.markdown :deep(img) { max-width: 100%; }
</style>
```

- [ ] **Step 2: 手动验收**

预期：`/job/{id}/read` 左侧章节树可切换、正文 Markdown（代码高亮/表格）渲染正常、术语表附录可用

- [ ] **Step 3: 构建 + Commit**

Run: `cd web; npm run build`
```bash
git add web/src/views/Reader.vue
git commit -m "feat(web): reader page with chapter tree and markdown rendering"
```

---

### Task 10: e2e 冒烟 + README 启动指南

**Files:**
- Modify: `README.md`（替换 stub）
- Test: 无新测试（冒烟为手动脚本）

**Interfaces:**
- Consumes: 全部前序产物
- Produces: 无新接口

- [ ] **Step 1: e2e 冒烟（用既有 FastAPI 产物，只读路径 + 任务创建校验）**

```powershell
# 终端 1
.\.venv\Scripts\quickstudy.exe serve
# 终端 2（服务起来后）
curl http://127.0.0.1:8600/api/jobs
curl http://127.0.0.1:8600/   # 返回 index.html（web/dist 已 build）
```

验收清单：
- 浏览器打开 `http://127.0.0.1:8600`，历史任务为空或展示既有 jobs.json
- 手工在 `workspace/jobs.json` 塞入一条 `{id:"demo1", url:"https://fastapi.tiangolo.com", task_id:"fastapi-tiangolo-com", status:"done", created_at:1, updated_at:1, error:"", skipped_demos:false, interrupted:false, cancel_requested:false, with_demos:true, max_pages:null}` 后刷新：首页出现卡片 → 点击进阅读页 → 20 章目录与正文正常渲染
- 该 demo 任务的详情接口 `/api/jobs/demo1` 返回完整 progress（crawl 151 页、20 章、成本数字非 0）

- [ ] **Step 2: 更新 README.md（替换现有 stub）**

```markdown
# quickstudy

把任何官方技术文档站变成一本面向初学者的中文学习手册（代码示例经无网络沙箱验证可运行）。

## 快速开始（Web 界面）

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev,m2,render,web]"
playwright install chromium        # 仅 JS 渲染站需要
cd web; npm install; npm run build; cd ..
# 配置环境变量（见 .env.example）：ANTHROPIC_BASE_URL / ANTHROPIC_AUTH_TOKEN 必填
.venv\Scripts\quickstudy.exe serve  # 打开 http://127.0.0.1:8600
```

输入文档站地址 → 自动爬取/组织/Demo/大纲 → **确认大纲后才消耗写作 token** → 在线阅读。

## CLI 模式

`quickstudy crawl|organize|demos|outline|book <url>`，详见 README.dev.md。

## 前置依赖

Python ≥3.11、Node.js（前端构建）、Docker Desktop（仅 Demo 沙箱，未启动自动跳过）。
```

- [ ] **Step 3: 全量回归 + Commit**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q`（全绿）
```bash
git add README.md
git commit -m "docs: README quickstart for web UI"
```

---

## Self-Review 记录

- Spec 覆盖：§2 架构→Task 1-6；§3 状态机/持久化/日志→Task 1/3/4；§4 API→Task 4；§5 三页面→Task 7/8/9；§6 错误处理→Task 3（Docker 跳过/失败落态）+ Task 4（400/404/409）；§7 测试→各 Task TDD + Task 10 冒烟；§8 依赖→Task 4（pyproject）+ Task 6（package.json）
- 类型一致：derive_progress 结构（Task 2 定义）= api get_job 的 progress 字段（Task 4）= JobDetail 的 d/cost/recentLog（Task 8）；FakeRunner 方法名与 Runner 一致（run_next/confirm/request_cancel）
- 已知裁剪：阅读页不含 Demo 索引附录（VitePress 导出里有；Web v1 YAGNI）；运行中取消仅标志位语义，不测并发时序
