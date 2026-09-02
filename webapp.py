from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from web.job import JobBusyError, JobManager, subprocess_runner
from web.render import (
    chapter_label,
    first_heading,
    list_tutorials,
    markdown_to_html,
    resolve_tutorial_file,
)

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))


class JobIn(BaseModel):
    source_type: str
    repo_url: str = ""
    local_dir: str = ""
    language: str = "Chinese"
    name: str = ""
    github_token: str = ""
    max_abstractions: int = Field(default=10, ge=1, le=20)


def create_app(
    *,
    output_dir: Path | None = None,
    runner=None,
    python_exe: str | None = None,
) -> FastAPI:
    output = Path(output_dir or (ROOT / "output"))
    manager = JobManager(
        output_dir=output,
        python_exe=python_exe or sys.executable,
        main_py=ROOT / "main.py",
        cwd=ROOT,
        runner=runner or subprocess_runner,
    )
    app = FastAPI(title="Quick Study")
    app.state.manager = manager
    app.state.output_dir = output

    static_dir = WEB_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

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

    @app.get("/api/jobs/current")
    def api_current_job():
        return manager.snapshot()

    @app.post("/api/jobs")
    def api_start_job(body: JobIn):
        try:
            return manager.start(body.model_dump())
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/current/events")
    async def api_job_events():
        async def generate():
            last = 0
            while True:
                snap = manager.snapshot()
                logs = snap.get("logs") or []
                if last < len(logs):
                    for line in logs[last:]:
                        yield f"data: {json.dumps({'type': 'log', 'line': line}, ensure_ascii=False)}\n\n"
                    last = len(logs)
                if snap["status"] != "running":
                    yield f"data: {json.dumps({'type': 'done', 'status': snap['status'], 'output_name': snap.get('output_name'), 'error': snap.get('error')}, ensure_ascii=False)}\n\n"
                    break
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
        return TEMPLATES.TemplateResponse(
            request,
            "tutorial.html",
            {
                "title": title,
                "tutorial_name": tutorial_name,
                "filename": filename,
                "body": markdown_to_html(text, tutorial_name),
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
