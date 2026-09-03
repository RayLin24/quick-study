from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from utils.ask_tutorial import AskRefused, ask_tutorial
from web.bind import (
    BindRefused,
    assert_safe_bind,
    detect_uvicorn_host,
    is_loopback_host,
    token_from_headers,
)
from web.job import JobBusyError, JobManager, subprocess_runner
from web.render import (
    add_h2_ids,
    chapter_label,
    first_heading,
    list_tutorials,
    markdown_to_html,
    resolve_tutorial_file,
)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Quick Study 登录</title>
<link rel="stylesheet" href="/static/app.css"></head>
<body><main class="layout"><section class="panel">
<h1>需要访问令牌</h1>
<p>非本机回环绑定必须设置 <code>QUICK_STUDY_TOKEN</code>。</p>
<form method="post" action="/login">
<label class="field"><span>令牌</span>
<input type="password" name="token" autocomplete="off"></label>
<button type="submit">进入</button>
</form>
<p class="error">{error}</p>
</section></main></body></html>
"""


class JobIn(BaseModel):
    source_type: str
    repo_url: str = ""
    local_dir: str = ""
    language: str = "Chinese"
    name: str = ""
    github_token: str = ""
    max_abstractions: int = Field(default=10, ge=1, le=20)
    include: str = ""
    exclude: str = ""
    max_size: int | None = Field(default=None, ge=1)


class AskIn(BaseModel):
    question: str = ""


def create_app(
    *,
    output_dir: Path | None = None,
    runner=None,
    python_exe: str | None = None,
    bind_host: str | None = None,
    ask_fn=None,
) -> FastAPI:
    output = Path(output_dir or (ROOT / "output"))
    manager = JobManager(
        output_dir=output,
        python_exe=python_exe or sys.executable,
        main_py=ROOT / "main.py",
        cwd=ROOT,
        runner=runner or subprocess_runner,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        host = bind_host if bind_host is not None else detect_uvicorn_host()
        if host is not None:
            try:
                assert_safe_bind(host)
            except BindRefused as exc:
                raise RuntimeError(str(exc)) from exc
            app.state.require_auth = not is_loopback_host(host)
        yield

    app = FastAPI(title="Quick Study", lifespan=lifespan)
    app.state.manager = manager
    app.state.output_dir = output
    app.state.require_auth = False
    app.state.ask_fn = ask_fn or ask_tutorial

    static_dir = WEB_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def auth_if_needed(request: Request, call_next):
        if not getattr(app.state, "require_auth", False):
            return await call_next(request)
        path = request.url.path
        if path in {"/login", "/healthz"} or path.startswith("/static/"):
            return await call_next(request)
        expected = (os.getenv("QUICK_STUDY_TOKEN") or "").strip()
        got = token_from_headers(request.headers, request.cookies)
        if expected and got == expected:
            return await call_next(request)
        if path.startswith("/api/"):
            return JSONResponse({"detail": "需要 QUICK_STUDY_TOKEN"}, status_code=401)
        return HTMLResponse(LOGIN_HTML.format(error=""), status_code=401)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/login", response_class=HTMLResponse)
    def login_form():
        return HTMLResponse(LOGIN_HTML.format(error=""))

    @app.post("/login")
    async def login_submit(request: Request):
        form = await request.form()
        token = str(form.get("token") or "").strip()
        expected = (os.getenv("QUICK_STUDY_TOKEN") or "").strip()
        if not expected or token != expected:
            return HTMLResponse(LOGIN_HTML.format(error="令牌不正确"), status_code=401)
        response = HTMLResponse('<meta http-equiv="refresh" content="0; url=/">')
        response.set_cookie("quick_study_token", token, httponly=True, samesite="lax")
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {"tutorials": list_tutorials(output)},
        )

    @app.get("/api/tutorials")
    def api_tutorials():
        return {"items": list_tutorials(output)}

    @app.post("/api/tutorials/{tutorial_name}/ask")
    def api_ask(tutorial_name: str, body: AskIn):
        try:
            resolve_tutorial_file(output, tutorial_name, "index.md")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=400,
                detail="还没有生成这篇教程，无法提问。请先生成教程。",
            ) from exc
        folder = output / tutorial_name
        try:
            answer = app.state.ask_fn(folder, body.question)
        except AskRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"answer": answer}

    @app.get("/api/jobs/current")
    def api_current_job(after: int | None = None):
        return manager.snapshot(after=after)

    @app.post("/api/jobs")
    def api_start_job(body: JobIn):
        try:
            return manager.start(body.model_dump())
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/current/cancel")
    def api_cancel_job():
        try:
            return manager.cancel()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/current/events")
    async def api_job_events(after: int = 0):
        async def generate():
            last = max(0, after)
            last_beat = 0.0
            while True:
                snap = manager.snapshot(after=last)
                logs = snap.get("logs") or []
                last = snap.get("log_cursor", last)
                for line in logs:
                    yield f"data: {json.dumps({'type': 'log', 'line': line, 'step': snap.get('step')}, ensure_ascii=False)}\n\n"
                if snap["status"] != "running":
                    payload = {
                        "type": "done",
                        "status": snap["status"],
                        "output_name": snap.get("output_name"),
                        "error": snap.get("error"),
                        "file_count": snap.get("file_count"),
                        "map_mode": snap.get("map_mode"),
                        "usage": snap.get("usage"),
                        "step": snap.get("step"),
                    }
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                now = asyncio.get_event_loop().time()
                if now - last_beat >= 5:
                    last_beat = now
                    yield f"data: {json.dumps({'type': 'heartbeat', 'step': snap.get('step')})}\n\n"
                await asyncio.sleep(0.35)

        return StreamingResponse(generate(), media_type="text/event-stream")

    @app.get("/t/{tutorial_name}", response_class=HTMLResponse)
    def tutorial_index(request: Request, tutorial_name: str):
        return _tutorial_page(request, tutorial_name, "index.md")

    @app.get("/t/{tutorial_name}/{filename}", response_class=HTMLResponse)
    def tutorial_chapter(request: Request, tutorial_name: str, filename: str):
        return _tutorial_page(request, tutorial_name, filename)

    def _tutorial_page(request: Request, tutorial_name: str, filename: str):
        try:
            path = resolve_tutorial_file(output, tutorial_name, filename)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        text = path.read_text(encoding="utf-8")
        heading = first_heading(text)
        title = chapter_label(filename, heading) if filename != "index.md" else (heading or tutorial_name)
        chapters = _chapter_links(output, tutorial_name)
        prev_chapter, next_chapter = _neighbors(chapters, filename)
        body = markdown_to_html(
            text,
            tutorial_name,
            cover=filename == "index.md",
        )
        _body, page_toc = add_h2_ids(body)
        return TEMPLATES.TemplateResponse(
            request,
            "tutorial.html",
            {
                "title": title,
                "tutorial_name": tutorial_name,
                "filename": filename,
                "body": body,
                "page_toc": page_toc,
                "chapters": chapters,
                "prev_chapter": prev_chapter,
                "next_chapter": next_chapter,
            },
        )

    return app


def _chapter_links(output_dir: Path, tutorial_name: str) -> list[dict]:
    folder = output_dir / tutorial_name
    if not folder.is_dir():
        return []
    items = [{"title": "目录", "href": f"/t/{tutorial_name}", "filename": "index.md", "number": "0"}]
    for path in sorted(folder.glob("*.md")):
        if path.name in {"index.md", "README.md"}:
            continue
        heading = first_heading(path.read_text(encoding="utf-8"))
        number = path.stem.split("_", 1)[0] if path.stem[:1].isdigit() else ""
        items.append(
            {
                "title": chapter_label(path.name, heading),
                "href": f"/t/{tutorial_name}/{path.name}",
                "filename": path.name,
                "number": number.lstrip("0") or number,
            }
        )
    return items


def _neighbors(chapters: list[dict], filename: str) -> tuple[dict | None, dict | None]:
    index = next((i for i, item in enumerate(chapters) if item["filename"] == filename), None)
    if index is None:
        return None, None
    prev_item = chapters[index - 1] if index > 0 else None
    next_item = chapters[index + 1] if index + 1 < len(chapters) else None
    return prev_item, next_item


app = create_app()
