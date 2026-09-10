from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from utils.annotations import add_annotation, load_annotations
from utils.ask_thread import ask_with_thread
from utils.ask_tutorial import AskRefused, AskResult, ask_tutorial_detailed
from utils.auth_errors import AUTH_READ_ONLY, AUTH_UNAUTHORIZED, read_only, unauthorized
from utils.audit_log import actor_id, append_audit, list_audit
from utils.budget import BudgetExceeded, assert_budget
from utils.commit_range import commit_range_guide
from utils.demo_mode import demo_enabled, demo_payload, is_generate_path
from utils.export_epub import build_epub
from utils.export_obsidian import build_obsidian_zip, notion_index_markdown
from utils.feed import atom_feed, rss_feed
from utils.import_pack import ImportRefused, import_tutorial_zip
from utils.ip_rate_limit import RateLimited, bucket_key, limiter_from_env
from utils.jsonl_log import emit_run, tail_run
from utils.key_rotation import detect_key_files
from utils.otel import span
from utils.continue_reading import continue_card, load_progress_file, save_progress_file
from utils.disaster_recovery import backup_output, restore_output
from utils.entry_files import pick_entry_files
from utils.exercises import tutorial_exercises
from utils.mem_guard import suggest_concurrency
from utils.og_cover import og_image_url
from utils.presets import dump_preset, list_presets, load_preset, save_preset
from utils.stale import stale_status
from utils.test_policy import classify_files, policy_markdown
from utils.tutorial_search import search_tutorials
from utils.tutorial_tags import list_groups, set_tags
from utils.week_path import week_path, week_path_markdown
from utils.cost_hint import max_abstractions_cost_hint
from utils.editor_open import resolve_editor_url
from utils.graph_color import color_mermaid
from utils.i18n import catalog, normalize_ui_lang
from utils.job_history import list_history
from utils.learn_outcomes import learning_outcomes
from utils.local_watch import watch_status
from utils.next_chapter import recommend_next
from utils.provider_cost import estimate_from_usage, estimate_preview_calls
from utils.quality_score import score_tutorial
from utils.repo_diff_guide import compare_repos
from utils.retry_chapter import failed_chapters, mark_chapter_for_retry
from utils.deepwiki import deepwiki_url
from utils.digest import gitingest_digest
from utils.errors import format_error
from utils.export_tutorial import build_llms_txt, zip_tutorial
from utils.favorites import load_favorites, toggle_favorite
from utils.glossary import build_glossary
from utils.health import health_payload
from utils.heatmap import heatmap_markdown
from utils.language import DEFAULT_LANGUAGE
from utils.mcp_tools import call_mcp_tool, mcp_catalog
from utils.mermaid_export import export_mermaid
from utils.offline_html import build_offline_html
from utils.pat_wizard import classify_pat, wizard_steps
from utils.preview import PreviewError, preview_generation
from utils.pr_guide import build_pr_guide_prompt
from utils.push_hook import hook_secret, incremental_job_from_push, verify_github_signature
from utils.quiz import tutorial_quizzes
from utils.readonly_token import classify_token, is_write_path
from utils.strategy import INCLUDE_PRESETS, STRATEGIES
from utils.versions import diff_versions, list_versions, read_version_file
from utils.workbench import load_workbench, save_workbench
from utils.zip_source import extract_source_zip
from web.bind import (
    BindRefused,
    assert_safe_bind,
    detect_uvicorn_host,
    is_loopback_host,
    token_from_headers,
)
from web.job import JobBusyError, JobManager, subprocess_runner, validate_start_request
from web.render import (
    chapter_label,
    first_heading,
    list_tutorials,
    markdown_to_html,
    parse_truncation_note,
    resolve_tutorial_file,
)
from web.tutorial_ops import clear_tutorial_cache, delete_tutorial, rename_tutorial, tutorial_folder

ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
TEMPLATES = Jinja2Templates(directory=str(WEB_DIR / "templates"))
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>Quick Study 登录</title>
<link rel="stylesheet" href="/static/app.css"></head>
<body><main class="layout"><section class="panel">
<div class="bind-warn" role="alert">
<strong>公网 / 局域网绑定必须带令牌</strong>
<p>未设置 <code>QUICK_STUDY_TOKEN</code> 时，非 127.0.0.1 绑定会拒绝启动，防止未授权的 LLM 调用。</p>
</div>
<h1>需要访问令牌</h1>
<p>请输入环境变量 <code>QUICK_STUDY_TOKEN</code> 的值。</p>
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
    language: str = DEFAULT_LANGUAGE
    name: str = ""
    github_token: str = ""
    max_abstractions: int = Field(default=10, ge=1, le=20)
    include: str = ""
    exclude: str = ""
    max_size: int | None = Field(default=None, ge=1)
    resume: bool = False
    incremental: bool = False
    overview_only: bool = False
    polish: bool = False
    replace: bool = False
    strategy: str = ""
    learning_goal: str = ""
    bilingual: bool = False
    pagerank_order: bool = False
    seed_files: list[str] = Field(default_factory=list)
    queue: bool = False
    retry_chapter: str = ""
    confirm_outline: bool | None = None


class RenameIn(BaseModel):
    name: str = ""


class AskIn(BaseModel):
    question: str = ""
    thread: bool = False


class AnnotationIn(BaseModel):
    filename: str = "index.md"
    quote: str = ""
    note: str = ""
    author: str = ""


class PresetIn(BaseModel):
    name: str = "default"
    payload: dict = Field(default_factory=dict)


class BackupIn(BaseModel):
    dest: str = ""


class RestoreIn(BaseModel):
    archive: str = ""


class ProgressIn(BaseModel):
    path: str = ""
    ts: float | None = None


class PolicyIn(BaseModel):
    files: list[str] = Field(default_factory=list)
    tests_as_chapter: bool = False


class EntriesIn(BaseModel):
    files: list = Field(default_factory=list)


class WorkbenchIn(BaseModel):
    repos: list[dict] = Field(default_factory=list)


class PatIn(BaseModel):
    token: str = ""


class DigestIn(BaseModel):
    files: list[dict] = Field(default_factory=list)


class CompareIn(BaseModel):
    left: list[dict] = Field(default_factory=list)
    right: list[dict] = Field(default_factory=list)


class McpCallIn(BaseModel):
    name: str = ""
    arguments: dict = Field(default_factory=dict)


class CompareReposIn(BaseModel):
    left: str = ""
    right: str = ""


class CommitRangeIn(BaseModel):
    local_dir: str = ""
    since: str = ""
    until: str = "HEAD"


class ColorGraphIn(BaseModel):
    source: str = ""
    files: list[str] = Field(default_factory=list)
    by: str = "lang"


class EditorIn(BaseModel):
    path: str = ""
    local_dir: str = ""
    line: int | None = None


class RetryChapterIn(BaseModel):
    filename: str = ""


class TagsIn(BaseModel):
    tags: list[str] = Field(default_factory=list)
    group: str = ""


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
    app.state.ask_fn = ask_fn or ask_tutorial_detailed
    app.state.rate_limiter = limiter_from_env()

    static_dir = WEB_DIR / "static"
    static_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.middleware("http")
    async def rate_limit_and_demo(request: Request, call_next):
        path = request.url.path
        if path.startswith("/static/") or path in {"/healthz", "/api/config"}:
            return await call_next(request)
        if demo_enabled() and is_generate_path(request.method, path):
            return JSONResponse({"detail": demo_payload()["detail"], "code": "demo"}, status_code=403)
        try:
            token = token_from_headers(request.headers, request.cookies)
            app.state.rate_limiter.check(bucket_key(ip=request.client.host if request.client else "", token=token))
        except RateLimited as exc:
            return JSONResponse({"detail": str(exc), "code": "rate_limited"}, status_code=429)
        return await call_next(request)

    @app.middleware("http")
    async def auth_if_needed(request: Request, call_next):
        if not getattr(app.state, "require_auth", False):
            return await call_next(request)
        path = request.url.path
        if path in {"/login", "/healthz", "/api/config"} or path.startswith("/static/"):
            return await call_next(request)
        role = classify_token(request.headers, request.cookies)
        if role == "write":
            return await call_next(request)
        if role == "read":
            if is_write_path(request.method, path):
                body = read_only()
                return JSONResponse({"detail": body["detail"], "code": AUTH_READ_ONLY}, status_code=403)
            return await call_next(request)
        if path.startswith("/api/"):
            body = unauthorized("需要 QUICK_STUDY_TOKEN")
            return JSONResponse({"detail": body["detail"], "code": AUTH_UNAUTHORIZED}, status_code=401)
        return HTMLResponse(LOGIN_HTML.format(error=""), status_code=401)

    @app.get("/healthz")
    def healthz():
        return health_payload(bind_host=bind_host, output_dir=output)

    @app.get("/api/config")
    def api_config():
        return {
            "llm_timeout_seconds": float(os.getenv("LLM_TIMEOUT_SECONDS") or 300),
            "mermaid_version": "11.4.1",
            "smoke_repo": "https://github.com/octocat/Hello-World",
            "max_abstractions_hint": max_abstractions_cost_hint(16),
            "pat_steps": wizard_steps(),
            "i18n": catalog("zh"),
            "cost_rates": estimate_from_usage({"prompt_tokens": 0, "completion_tokens": 0}),
        }

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
        lang = normalize_ui_lang(request.cookies.get("qs_lang") or request.query_params.get("lang"))
        return TEMPLATES.TemplateResponse(
            request,
            "index.html",
            {
                "tutorials": list_tutorials(output),
                "default_language": DEFAULT_LANGUAGE,
                "strategies": STRATEGIES,
                "include_presets": INCLUDE_PRESETS,
                "i18n": catalog(lang),
                "ui_lang": lang,
                "continue_card": continue_card(output, load_progress_file(output)),
            },
        )

    @app.get("/api/tutorials")
    def api_tutorials():
        return {"items": list_tutorials(output)}

    @app.post("/api/tutorials/{tutorial_name}/ask")
    def api_ask(tutorial_name: str, body: AskIn, request: Request):
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
            assert_budget(output)
            actor = actor_id(
                token=token_from_headers(request.headers, request.cookies),
                session=request.cookies.get("quick_study_token") or "",
                ip=request.client.host if request.client else "",
            )
            append_audit(output, action="ask", actor=actor, detail={"tutorial": tutorial_name})
            emit_run(output, "ask", tutorial=tutorial_name)
            with span("ask", output_dir=output, attributes={"tutorial": tutorial_name}):
                if body.thread:
                    raw = ask_with_thread(folder, body.question)
                else:
                    raw = app.state.ask_fn(folder, body.question)
        except BudgetExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except AskRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=400, detail=format_error(exc)) from exc
        if isinstance(raw, AskResult):
            return {
                "answer": raw.answer,
                "used_chapters": raw.used_chapters,
                "routed": raw.routed,
                "citations": raw.citations,
                "markdown": raw.markdown or raw.answer,
                "degraded": raw.degraded,
                "attempts": raw.attempts,
            }
        if isinstance(raw, dict) and "answer" in raw:
            return {
                "answer": raw["answer"],
                "used_chapters": raw.get("used_chapters") or [],
                "routed": bool(raw.get("routed")),
                "citations": raw.get("citations") or [],
                "markdown": raw.get("markdown") or raw["answer"],
                "degraded": bool(raw.get("degraded")),
                "attempts": raw.get("attempts") or 1,
            }
        return {
            "answer": raw,
            "used_chapters": [],
            "routed": False,
            "citations": [],
            "markdown": raw,
            "degraded": False,
            "attempts": 1,
        }

    @app.post("/api/jobs/preview")
    def api_preview_job(body: JobIn):
        try:
            data = validate_start_request(body.model_dump())
            preview = preview_generation(data)
            preview["cost"] = estimate_preview_calls(preview.get("estimated_calls") or {})
            return preview
        except PreviewError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/current")
    def api_current_job(after: int | None = None):
        return manager.snapshot(after=after)

    @app.post("/api/jobs")
    def api_start_job(body: JobIn):
        try:
            return manager.start(body.model_dump())
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except BudgetExceeded as exc:
            raise HTTPException(status_code=429, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/queue")
    def api_job_queue():
        return {"items": manager.queue_snapshot()}

    @app.get("/api/jobs/history")
    def api_job_history(limit: int = 50):
        return {"items": manager.history(limit)}

    @app.get("/jobs", response_class=HTMLResponse)
    def jobs_page(request: Request):
        lang = normalize_ui_lang(request.cookies.get("qs_lang") or request.query_params.get("lang"))
        return TEMPLATES.TemplateResponse(
            request,
            "jobs.html",
            {"items": list_history(output), "i18n": catalog(lang), "ui_lang": lang},
        )

    @app.post("/api/jobs/current/pause")
    def api_pause_job():
        try:
            return manager.pause()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/current/resume")
    def api_resume_job():
        try:
            return manager.resume()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/jobs/current/outline")
    def api_current_outline():
        return manager.outline_snapshot()

    @app.post("/api/jobs/current/confirm-outline")
    def api_confirm_outline():
        try:
            return manager.confirm_current_outline()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/budget")
    def api_budget():
        return assert_budget(output)

    @app.delete("/api/tutorials/{tutorial_name}")
    def api_delete_tutorial(tutorial_name: str):
        try:
            delete_tutorial(output, tutorial_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        return {"ok": True}

    @app.post("/api/tutorials/{tutorial_name}/rename")
    def api_rename_tutorial(tutorial_name: str, body: RenameIn):
        try:
            new_name = rename_tutorial(output, tutorial_name, body.name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        return {"ok": True, "name": new_name}

    @app.get("/api/tutorials/{tutorial_name}/llms.txt")
    def api_llms_txt(tutorial_name: str):
        try:
            folder = tutorial_folder(output, tutorial_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(build_llms_txt(folder), media_type="text/plain")

    @app.get("/api/tutorials/{tutorial_name}/export.zip")
    def api_export_zip(tutorial_name: str, request: Request):
        try:
            folder = tutorial_folder(output, tutorial_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        from fastapi.responses import Response

        data = zip_tutorial(folder)
        actor = actor_id(token=token_from_headers(request.headers, request.cookies), ip=request.client.host if request.client else "")
        append_audit(output, action="export_zip", actor=actor, detail={"tutorial": tutorial_name})
        return Response(
            data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{tutorial_name}.zip"'},
        )

    @app.post("/api/tutorials/{tutorial_name}/cache/clear")
    def api_clear_cache(tutorial_name: str):
        try:
            folder = tutorial_folder(output, tutorial_name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="找不到这篇教程") from exc
        cleared = clear_tutorial_cache(folder)
        meta = {}
        meta_path = folder / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        return {"ok": True, "cleared": cleared, "include_hash": meta.get("include_hash")}

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
        truncation = parse_truncation_note(text)
        meta = {}
        meta_path = (output / tutorial_name / "meta.json")
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        from utils.diagram_nodes import load_diagram_nodes

        body, page_toc = markdown_to_html(
            text,
            tutorial_name,
            cover=filename == "index.md",
            repo_url=meta.get("repo_url"),
            local_dir=meta.get("local_dir"),
            sha=meta.get("upstream_commit"),
            return_toc=True,
        )
        diagram_nodes = load_diagram_nodes(output / tutorial_name, chapters)
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
                "truncation": truncation,
                "repo_url": meta.get("repo_url"),
                "dead_links": meta.get("dead_links") or [],
                "relationship_warnings": meta.get("relationship_warnings") or {},
                "deepwiki": deepwiki_url(meta.get("repo_url")),
                "llm_timeout": float(os.getenv("LLM_TIMEOUT_SECONDS") or 300),
                "outcomes": learning_outcomes(text, language=str(meta.get("language") or "Chinese")),
                "next_smart": recommend_next(output / tutorial_name, filename) if filename != "index.md" else {},
                "quality": score_tutorial(output / tutorial_name) if filename == "index.md" else {},
                "stale": stale_status(output / tutorial_name),
                "diagram_nodes": diagram_nodes,
                "upstream_commit": meta.get("upstream_commit"),
                "og_image": og_image_url(meta.get("repo_url")),
                "week_path": week_path(output / tutorial_name) if filename == "index.md" else {},
                "i18n": catalog(normalize_ui_lang(request.cookies.get("qs_lang"))),
                "ui_lang": normalize_ui_lang(request.cookies.get("qs_lang")),
            },
        )

    @app.get("/embed/{tutorial_name}", response_class=HTMLResponse)
    def embed_tutorial(request: Request, tutorial_name: str):
        return _tutorial_page(request, tutorial_name, "index.md")

    @app.get("/api/tutorials/{tutorial_name}/offline.html")
    def api_offline_html(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return HTMLResponse(build_offline_html(folder))

    @app.post("/api/tutorials/{tutorial_name}/mermaid/export")
    def api_mermaid_export(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return export_mermaid(folder)

    @app.get("/api/tutorials/{tutorial_name}/versions")
    def api_versions(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return {"items": list_versions(folder)}

    @app.get("/api/tutorials/{tutorial_name}/versions/{older}/diff/{newer}")
    def api_version_diff(tutorial_name: str, older: str, newer: str):
        folder = tutorial_folder(output, tutorial_name)
        return PlainTextResponse(diff_versions(folder, older, newer))

    @app.get("/api/tutorials/{tutorial_name}/glossary")
    def api_glossary(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return PlainTextResponse(build_glossary(folder))

    @app.get("/api/tutorials/{tutorial_name}/heatmap")
    def api_heatmap(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        names = [item["title"] for item in _chapter_links(output, tutorial_name)[1:]]
        edges = []
        meta_path = folder / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                edges = (meta.get("relationship_warnings") or {}).get("edges") or []
            except (OSError, json.JSONDecodeError):
                edges = []
        return PlainTextResponse(heatmap_markdown(names, edges if isinstance(edges, list) else []))

    @app.get("/api/tutorials/{tutorial_name}/quiz")
    def api_quiz(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return {"items": tutorial_quizzes(folder)}

    @app.get("/api/tutorials/{tutorial_name}/annotations")
    def api_get_annotations(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return {"items": load_annotations(folder)}

    @app.post("/api/tutorials/{tutorial_name}/annotations")
    def api_add_annotation(tutorial_name: str, body: AnnotationIn):
        folder = tutorial_folder(output, tutorial_name)
        return {
            "items": add_annotation(
                folder,
                filename=body.filename,
                quote=body.quote,
                note=body.note,
                author=body.author or "local",
            )
        }

    @app.post("/api/tutorials/{tutorial_name}/star")
    def api_star(tutorial_name: str):
        tutorial_folder(output, tutorial_name)
        return {"names": toggle_favorite(output, tutorial_name)}

    @app.get("/api/favorites")
    def api_favorites():
        return {"names": load_favorites(output)}

    @app.get("/mcp/tools")
    def api_mcp_tools():
        return mcp_catalog()

    @app.post("/mcp/call")
    def api_mcp_call(body: McpCallIn):
        try:
            return call_mcp_tool(output, body.name, body.arguments)
        except (AskRefused, ValueError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/workbench")
    def api_workbench():
        return {"repos": load_workbench(output)}

    @app.post("/api/workbench")
    def api_save_workbench(body: WorkbenchIn):
        return {"repos": save_workbench(output, body.repos)}

    @app.post("/api/jobs/upload")
    async def api_upload_zip(file: UploadFile = File(...)):
        blob = await file.read()
        dest = output / ".uploads" / (file.filename or "src").replace("..", "_")
        folder = extract_source_zip(blob, dest)
        payload = {"source_type": "dir", "local_dir": str(folder)}
        try:
            return manager.start(payload)
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/pat/check")
    def api_pat_check(body: PatIn):
        return classify_pat(body.token)

    @app.post("/api/digest")
    def api_digest(body: DigestIn):
        files = [(str(item.get("path") or "file"), str(item.get("content") or "")) for item in body.files]
        return {"digest": gitingest_digest(files)}

    @app.post("/api/abstractions/compare")
    def api_compare(body: CompareIn):
        from utils.abstraction_compare import compare_abstractions

        return compare_abstractions(body.left, body.right)

    @app.post("/api/guides/pr")
    def api_pr_guide(body: dict):
        return {"prompt": build_pr_guide_prompt(str(body.get("diff") or ""), title=str(body.get("title") or "PR"))}

    @app.post("/api/hooks/github")
    async def api_github_hook(request: Request):
        payload = await request.body()
        secret = hook_secret()
        if secret:
            sig = request.headers.get("x-hub-signature-256") or ""
            if not verify_github_signature(secret, payload, sig):
                raise HTTPException(status_code=401, detail="invalid signature")
        event = json.loads(payload.decode("utf-8") or "{}")
        job = incremental_job_from_push(event)
        try:
            return manager.start(job)
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/i18n")
    def api_i18n(lang: str = "zh"):
        return catalog(lang)

    @app.get("/api/tutorials/{tutorial_name}/quality")
    def api_quality(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return score_tutorial(folder)

    @app.get("/api/tutorials/{tutorial_name}/next")
    def api_next_chapter(tutorial_name: str, filename: str = "index.md"):
        folder = tutorial_folder(output, tutorial_name)
        return recommend_next(folder, filename)

    @app.get("/api/tutorials/{tutorial_name}/watch")
    def api_watch(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return watch_status(folder)

    @app.get("/api/tutorials/{tutorial_name}/failed-chapters")
    def api_failed_chapters(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return {"items": failed_chapters(folder)}

    @app.post("/api/tutorials/{tutorial_name}/retry-chapter")
    def api_retry_chapter(tutorial_name: str, body: RetryChapterIn):
        folder = tutorial_folder(output, tutorial_name)
        cleared = mark_chapter_for_retry(folder, body.filename)
        meta = {}
        meta_path = folder / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {}
        payload = {
            "source_type": "repo" if meta.get("repo_url") else "dir",
            "repo_url": meta.get("repo_url") or "",
            "local_dir": meta.get("local_dir") or "",
            "name": meta.get("name") or tutorial_name,
            "language": meta.get("language") or DEFAULT_LANGUAGE,
            "resume": True,
            "retry_chapter": body.filename,
            "queue": True,
        }
        if not payload["repo_url"] and not payload["local_dir"]:
            return {"ok": True, "cleared": cleared, "queued": False, "reason": "no_source"}
        try:
            started = manager.start(payload)
        except JobBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "cleared": cleared, "job": started}

    @app.post("/api/compare-repos")
    def api_compare_repos(body: CompareReposIn):
        left = tutorial_folder(output, body.left)
        right = tutorial_folder(output, body.right)
        return compare_repos(left, right, left_name=body.left, right_name=body.right)

    @app.post("/api/guides/commits")
    def api_commit_range(body: CommitRangeIn):
        try:
            return commit_range_guide(body.local_dir, body.since, body.until)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/graph/color")
    def api_color_graph(body: ColorGraphIn):
        return {"source": color_mermaid(body.source, body.files, by=body.by or "lang")}

    @app.post("/api/editor/open")
    def api_editor_open(body: EditorIn):
        return resolve_editor_url(body.path, local_dir=body.local_dir or None, line=body.line)

    @app.get("/api/search")
    def api_search(q: str = ""):
        return {"items": search_tutorials(output, q)}

    @app.post("/api/tutorials/{tutorial_name}/tags")
    def api_set_tags(tutorial_name: str, body: TagsIn):
        tutorial_folder(output, tutorial_name)
        return set_tags(output, tutorial_name, body.tags, body.group)

    @app.get("/api/library/tags")
    def api_library_tags():
        return list_groups(output)

    @app.post("/api/tutorials/import")
    async def api_import_pack(file: UploadFile = File(...), name: str = ""):
        blob = await file.read()
        try:
            result = import_tutorial_zip(blob, output, name=name or None)
        except ImportRefused as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return result

    @app.get("/api/tutorials/{tutorial_name}/obsidian.zip")
    def api_obsidian(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return Response(
            build_obsidian_zip(folder),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{tutorial_name}-obsidian.zip"'},
        )

    @app.get("/api/tutorials/{tutorial_name}/notion.md")
    def api_notion(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return PlainTextResponse(notion_index_markdown(folder))

    @app.get("/api/tutorials/{tutorial_name}/export.epub")
    def api_epub(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return Response(
            build_epub(folder),
            media_type="application/epub+zip",
            headers={"Content-Disposition": f'attachment; filename="{tutorial_name}.epub"'},
        )

    @app.get("/feed.xml")
    def api_atom():
        return Response(atom_feed(output), media_type="application/atom+xml")

    @app.get("/rss.xml")
    def api_rss():
        return Response(rss_feed(output), media_type="application/rss+xml")

    @app.get("/api/audit")
    def api_audit(limit: int = 100):
        return {"items": list_audit(output, limit=limit)}

    @app.get("/api/ops/keys")
    def api_key_rotation():
        return detect_key_files(ROOT)

    @app.get("/api/ops/logs")
    def api_run_logs(limit: int = 50):
        return {"items": tail_run(output, limit=limit)}

    @app.get("/api/demo")
    def api_demo():
        return demo_payload() if demo_enabled() else {"demo": False}

    @app.get("/api/tutorials/{tutorial_name}/stale")
    def api_stale(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return stale_status(folder)

    @app.post("/api/entries")
    def api_entries(body: EntriesIn):
        return {"items": pick_entry_files(body.files)}

    @app.post("/api/test-policy")
    def api_test_policy(body: PolicyIn):
        report = classify_files(body.files, tests_as_chapter=body.tests_as_chapter)
        report["markdown"] = policy_markdown(report)
        return report

    @app.get("/v1/tutorials")
    def v1_tutorials():
        return {"items": list_tutorials(output)}

    @app.post("/v1/tutorials/{tutorial_name}/ask")
    def v1_ask(tutorial_name: str, body: AskIn, request: Request):
        return api_ask(tutorial_name, body, request)

    @app.post("/v1/jobs")
    def v1_start_job(body: JobIn):
        return api_start_job(body)

    @app.get("/v1/jobs/current")
    def v1_current_job(after: int | None = None):
        return manager.snapshot(after=after)

    @app.get("/api/tutorials/{tutorial_name}/exercises")
    def api_exercises(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return {"items": tutorial_exercises(folder)}

    @app.get("/api/tutorials/{tutorial_name}/week-path")
    def api_week_path(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return week_path(folder)

    @app.get("/api/tutorials/{tutorial_name}/week-path.md")
    def api_week_path_md(tutorial_name: str):
        folder = tutorial_folder(output, tutorial_name)
        return PlainTextResponse(week_path_markdown(folder))

    @app.get("/api/continue")
    def api_continue():
        return continue_card(output, load_progress_file(output)) or {}

    @app.post("/api/continue")
    def api_save_continue(body: ProgressIn):
        data = load_progress_file(output)
        if body.path:
            data[body.path] = body.ts or __import__("time").time()
            save_progress_file(output, data)
        return continue_card(output, data) or {}

    @app.get("/api/presets")
    def api_list_presets():
        return {"items": list_presets(output)}

    @app.post("/api/presets")
    def api_save_preset(body: PresetIn):
        return save_preset(output, body.name or "default", body.payload or dump_preset({}))

    @app.get("/api/presets/{name}")
    def api_load_preset(name: str):
        try:
            return load_preset(output, name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="没有这个预设") from exc

    @app.post("/api/ops/backup")
    def api_backup(body: BackupIn):
        dest = Path(body.dest) if body.dest else None
        return backup_output(output, dest)

    @app.post("/api/ops/restore")
    def api_restore(body: RestoreIn):
        try:
            return restore_output(Path(body.archive), output)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/ops/memory")
    def api_memory(file_count: int = 0):
        return suggest_concurrency(file_count)

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
