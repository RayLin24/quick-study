"""端到端 M1 流水线单测：MockTransport 模拟整个 MkDocs 站点，验证发现→抓取→解析→报告。"""
import json

import httpx
import pytest

from quickstudy.config import TaskConfig
from quickstudy.pipeline import run_m1
from tests import fixtures


def site_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path.rstrip("/") or "/"
    routes = {
        "/robots.txt": (200, fixtures.ROBOTS_TXT),
        "/sitemap.xml": (200, fixtures.SITEMAP_XML),
        "/": (200, fixtures.mkdocs_page("Home", "<p>Welcome. " + "intro " * 60 + "</p>")),
        "/learn": (200, fixtures.mkdocs_page("Learn", "<p>Learn page. " + "text " * 60 + "</p>")),
        "/tutorial/first-steps": (200, fixtures.mkdocs_page("First Steps", fixtures.DOC_BODY)),
        "/tutorial/path-params": (200, fixtures.mkdocs_page(
            "Path Parameters", "<h2>Path</h2><p>Path params. " + "x " * 60 + "</p>")),
        "/advanced/middleware": (200, fixtures.mkdocs_page(
            "Middleware", "<h2>Middleware</h2><p>Middleware. " + "y " * 60 + "</p>")),
        "/hidden-page": (200, fixtures.mkdocs_page("Hidden", "<p>hidden " * 60 + "</p>")),
        "/blog/launch": (200, fixtures.mkdocs_page("Blog", "<p>blog " * 60 + "</p>")),
    }
    if path in routes:
        status, text = routes[path]
        return httpx.Response(status, text=text)
    return httpx.Response(404, text="not found")


async def test_run_m1_end_to_end(tmp_path, monkeypatch):
    cfg = TaskConfig.load("https://docs.example.com/",
                          workspace=tmp_path, render_escalation=False, max_rps=1000)

    # 把 Fetcher 内部 client 换成 MockTransport：monkeypatch httpx.AsyncClient 默认构造
    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        kwargs["transport"] = httpx.MockTransport(site_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("quickstudy.fetch.fetcher.httpx.AsyncClient", mock_client)
    report = await run_m1(cfg)

    s = report["summary"]
    # sitemap 7 条：blog 被 SKIP 过滤；hidden-page 保留（软告警）
    assert s["discovered"] == 6, json.dumps(report["discovery_notes"], ensure_ascii=False)
    assert s["parsed_ok"] == 6
    assert s["coverage_l1"] == 1.0

    # 软告警：hidden-page 在 sitemap 但不在侧边栏
    soft_urls = [a["url"] for a in report["soft_alerts"]]
    assert any("hidden-page" in u for u in soft_urls)
    assert not report["hard_alerts"]

    # license 从页脚识别
    assert report["license"]["license"].startswith("CC-BY")

    # 产物落盘
    task_dir = cfg.task_dir
    assert (task_dir / "manifest.json").exists()
    assert (task_dir / "report.json").exists()
    manifest = json.loads((task_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["fingerprint"]["adapter"] == "mkdocs"
    first_steps = next(v for k, v in manifest["pages"].items() if "first-steps" in k)
    assert first_steps["parsed"] and first_steps["sidebar_index"] >= 0

    # parsed 产物含代码块与表格
    pid = first_steps["page_id"]
    doc = json.loads((task_dir / "parsed" / f"{pid}.json").read_text(encoding="utf-8"))
    assert doc["code_blocks"] and doc["tables"]
    assert (task_dir / "parsed" / f"{pid}.md").exists()


async def test_scope_suggestion_flat_site(tmp_path, monkeypatch):
    cfg = TaskConfig.load("https://docs.example.com/",
                          workspace=tmp_path, render_escalation=False, max_rps=1000)
    real_client = httpx.AsyncClient

    def mock_client(*args, **kwargs):
        kwargs.pop("timeout", None)
        kwargs["transport"] = httpx.MockTransport(site_handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr("quickstudy.fetch.fetcher.httpx.AsyncClient", mock_client)
    report = await run_m1(cfg)
    assert report["scope_suggestion"]["mode"] in ("flat", "single", "multi")
    # 本站以 /tutorial 为主但有多个分区，应给出候选清单
    assert report["scope_suggestion"]["candidates"]
