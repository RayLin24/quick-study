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
