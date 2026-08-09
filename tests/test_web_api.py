"""Web API 契约测试：ASGI 直连，runner 用假实现，不触网。"""
import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient

from quickstudy.web.api import create_app


class FakeRunner:
    def __init__(self):
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


async def test_create_requires_llm_env(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    app = create_app(workspace_root=tmp_path, runner=FakeRunner())
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
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
        await asyncio.sleep(0)                           # 让后台 confirm task 跑完
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
    (d / "chapters" / "01-入门.md").write_text("# 第1章", encoding="utf-8")
    (d / "glossary.json").write_text(json.dumps({"terms": {"FastAPI": {}}}))
    app = create_app(workspace_root=tmp_path, runner=FakeRunner())
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://t") as c:
        job = (await c.post("/api/jobs", json={"url": "https://a.com"})).json()
        r = await c.get(f"/api/jobs/{job['id']}/book")
        assert r.json()["chapters"][0]["filename"] == "01-入门.md"
        assert r.json()["glossary"]["terms"]
        r = await c.get(f"/api/jobs/{job['id']}/chapters/01-%E5%85%A5%E9%97%A8.md")
        assert r.json()["markdown"] == "# 第1章"
        r = await c.get(f"/api/jobs/{job['id']}/chapters/nope.md")
        assert r.status_code == 404
        r = await c.get(f"/api/jobs/{job['id']}/chapters/..%2Fsecret")
        assert r.status_code in (400, 404, 422)          # 路径穿越被拒


def test_serve_parser():
    from quickstudy.cli import build_parser
    args = build_parser().parse_args(["serve", "--port", "9900"])
    assert args.command == "serve" and args.port == 9900
    assert args.host == "127.0.0.1"


def test_load_env_from_dotenv(tmp_path, monkeypatch):
    """_load_env 从 .env 补缺；进程已有变量不被覆盖。"""
    from quickstudy.cli import _load_env
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://process-env")
    (tmp_path / ".env").write_text(
        "ANTHROPIC_BASE_URL=http://dotenv-file\nANTHROPIC_AUTH_TOKEN=from-dotenv\n",
        encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    _load_env()
    import os
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "from-dotenv"      # .env 补缺
    assert os.environ["ANTHROPIC_BASE_URL"] == "http://process-env"  # 进程优先
