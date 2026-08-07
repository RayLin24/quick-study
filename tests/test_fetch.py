"""抓取层单测：限速、robots、增量、JS 壳检测（全部走 httpx MockTransport，无外部网络）。"""
import httpx
import pytest

from quickstudy.config import TaskConfig
from quickstudy.fetch.fetcher import Fetcher, _looks_like_js_shell
from tests.fixtures import JS_SHELL_PAGE, ROBOTS_TXT, SITEMAP_XML, mkdocs_page


def make_fetcher(handler, **cfg_overrides) -> Fetcher:
    cfg = TaskConfig.load("https://docs.example.com/",
                          **{k: v for k, v in cfg_overrides.items() if v is not None})
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    return Fetcher(cfg, client=client)


def _routes(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS_TXT)
    if path == "/sitemap.xml":
        return httpx.Response(200, text=SITEMAP_XML)
    if path == "/internal/secret/":
        return httpx.Response(200, text="secret")
    if path == "/flaky/":
        return httpx.Response(500, text="boom")
    return httpx.Response(200, text=mkdocs_page("Page", "<p>content</p>"))


async def test_fetch_ok_with_metadata():
    async with make_fetcher(_routes) as f:
        r = await f.fetch("https://docs.example.com/learn/")
    assert r.ok and r.render == "http"
    assert r.content_hash and r.status == 200


async def test_robots_disallow_skips():
    async with make_fetcher(_routes) as f:
        r = await f.fetch("https://docs.example.com/internal/secret/")
    assert r.skipped == "robots" and not r.ok


async def test_incremental_304_unchanged():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=ROBOTS_TXT)
        if "if-none-match" in request.headers:
            return httpx.Response(304)
        return httpx.Response(200, text=mkdocs_page("P", "<p>x</p>"),
                              headers={"ETag": '"v1"'})

    async with make_fetcher(handler, incremental=True) as f:
        first = await f.fetch("https://docs.example.com/learn/")
        assert first.ok and first.etag == '"v1"'
        second = await f.fetch("https://docs.example.com/learn/",
                               known={"etag": first.etag, "content_hash": first.content_hash})
        assert second.skipped == "unchanged"


async def test_retry_gives_up_with_error():
    async with make_fetcher(_routes, max_retries=1) as f:
        r = await f.fetch("https://docs.example.com/flaky/")
    assert not r.ok and "500" in r.error


async def test_js_shell_detection_triggers_escalation_path():
    # 无 playwright 环境时渲染失败应回退为 http 结果而非崩溃
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=JS_SHELL_PAGE)

    async with make_fetcher(handler) as f:
        r = await f.fetch("https://spa.example.com/")
    assert r.ok and r.render == "http"  # 渲染不可用 → 保留原始快照


def test_js_shell_heuristic():
    assert _looks_like_js_shell(JS_SHELL_PAGE)
    assert not _looks_like_js_shell(mkdocs_page("Real", "<p>" + "word " * 300 + "</p>"))
