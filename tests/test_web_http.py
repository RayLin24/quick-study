from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import create_app


def _app(tmp_path: Path, runner):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return create_app(output_dir=output, runner=runner, python_exe="python")


def test_start_job_and_read_tutorial(tmp_path: Path):
    output = tmp_path / "output"

    def runner(cmd, on_line, cwd):
        dest = output / "repo"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "index.md").write_text("# Hello\n\n[Ch](01_one.md)\n", encoding="utf-8")
        (dest / "01_one.md").write_text("# One", encoding="utf-8")
        on_line(f"Tutorial generation complete! Files are in: {dest}")
        return 0

    client = TestClient(_app(tmp_path, runner))
    started = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo", "language": "Chinese"},
    )
    assert started.status_code == 200
    client.app.state.manager.wait(timeout=5)

    page = client.get("/t/repo")
    assert page.status_code == 200
    assert "Hello" in page.text
    assert 'href="/t/repo/01_one.md"' in page.text


def test_path_escape_is_rejected(tmp_path: Path):
    (tmp_path / "secret.md").write_text("nope", encoding="utf-8")
    tutorial = tmp_path / "output" / "Demo"
    tutorial.mkdir(parents=True)
    (tutorial / "index.md").write_text("# ok", encoding="utf-8")
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    res = client.get("/t/Demo/../secret.md")
    assert res.status_code in (400, 404)


def test_failed_job_exposes_exit_reason(tmp_path: Path):
    def runner(cmd, on_line, cwd):
        on_line("ValueError: OPENROUTER_API_KEY is not set. Copy .env.sample to .env")
        return 2

    client = TestClient(_app(tmp_path, runner))
    started = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo"},
    )
    assert started.status_code == 200
    client.app.state.manager.wait(timeout=5)
    snap = client.get("/api/jobs/current").json()
    assert snap["status"] == "failed"
    assert "进程退出码 2" in snap["error"]
    assert "OPENROUTER_API_KEY" in snap["error"]
    assert "QUICK_STUDY_ERROR:" in snap["error"]


def test_public_bind_without_token_refuses_to_start(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("QUICK_STUDY_TOKEN", raising=False)
    app = create_app(
        output_dir=tmp_path / "output",
        runner=lambda cmd, on_line, cwd: 0,
        python_exe="python",
        bind_host="0.0.0.0",
    )
    with pytest.raises(RuntimeError, match="QUICK_STUDY_TOKEN"):
        with TestClient(app):
            pass


def test_home_defaults_to_chinese(tmp_path: Path):
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    page = client.get("/")
    assert page.status_code == 200
    assert 'option value="Chinese" selected' in page.text
    assert 'name="include"' in page.text
    assert 'name="exclude"' in page.text
    assert 'name="max_size"' in page.text


def test_start_job_passes_shrink_params(tmp_path: Path):
    seen = {}

    def runner(cmd, on_line, cwd):
        seen["cmd"] = cmd
        return 0

    client = TestClient(_app(tmp_path, runner))
    res = client.post(
        "/api/jobs",
        json={
            "source_type": "repo",
            "repo_url": "https://github.com/owner/repo/tree/main",
            "include": "*.py",
            "exclude": "tests/*",
            "max_size": 12345,
        },
    )
    assert res.status_code == 200
    client.app.state.manager.wait(timeout=5)
    cmd = seen["cmd"]
    assert "--include" in cmd and "*.py" in cmd
    assert "--exclude" in cmd and "tests/*" in cmd
    assert "--max-size" in cmd and "12345" in cmd
    assert "--token" not in cmd


def test_open_jbeval_fixture_tutorial(tmp_path: Path):
    src = Path(__file__).resolve().parents[1] / ".jbeval" / "export-run1"
    dest = tmp_path / "output" / "export-run1"
    dest.mkdir(parents=True)
    for path in src.glob("*.md"):
        (dest / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")

    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    listed = client.get("/api/tutorials").json()
    assert "export-run1" in [item["name"] for item in listed["items"]]
    assert any(item.get("mtime") for item in listed["items"] if item["name"] == "export-run1")

    index = client.get("/t/export-run1")
    assert index.status_code == 200
    assert "DSH" in index.text or "测试" in index.text
    assert "学习路线" in index.text
    assert 'href="/t/export-run1/01-测试概览.md"' in index.text

    chapter = client.get("/t/export-run1/01-测试概览.md")
    assert chapter.status_code == 200
    assert "测试概览" in chapter.text
    assert "<h1>" in chapter.text
    assert "```" not in chapter.text or "<pre>" in chapter.text
    assert '<figure class="cover">' not in chapter.text
    assert "cdn.jsdelivr.net" not in chapter.text
    assert "/static/vendor/mermaid.min.js" in chapter.text
    assert "/static/vendor/highlight.min.js" in chapter.text
    vendor = Path(__file__).resolve().parents[1] / "web" / "static" / "vendor"
    assert (vendor / "mermaid.min.js").is_file()
    assert (vendor / "highlight.min.js").is_file()
    assert (vendor / "github.min.css").is_file()
    mermaid_head = (vendor / "mermaid.min.js").read_text(encoding="utf-8", errors="replace")[:80]
    assert not mermaid_head.lstrip().startswith("<")


def test_cancel_endpoint_stops_running_job(tmp_path: Path):
    gate = tmp_path / "gate"

    def runner(cmd, on_line, cwd):
        on_line("working")
        while not gate.exists():
            pass
        return 0

    client = TestClient(_app(tmp_path, runner))
    started = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo"},
    )
    assert started.status_code == 200
    cancelled = client.post("/api/jobs/current/cancel")
    assert cancelled.status_code == 200
    gate.write_text("ok", encoding="utf-8")
    client.app.state.manager.wait(timeout=5)
    snap = client.get("/api/jobs/current").json()
    assert snap["status"] == "cancelled"
    assert "QUICK_STUDY_ERROR:" in (snap.get("error") or "")


def test_success_snapshot_includes_usage(tmp_path: Path):
    output = tmp_path / "output"

    def runner(cmd, on_line, cwd):
        dest = output / "repo"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "index.md").write_text("# Hello\n", encoding="utf-8")
        on_line("QUICK_STUDY_STEP: combine")
        on_line("QUICK_STUDY_USAGE: prompt=12 completion=34 total=46 calls=3 max_tokens=8192")
        on_line(f"Tutorial generation complete! Files are in: {dest}")
        return 0

    client = TestClient(_app(tmp_path, runner))
    started = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo"},
    )
    assert started.status_code == 200
    client.app.state.manager.wait(timeout=5)
    snap = client.get("/api/jobs/current").json()
    assert snap["status"] == "succeeded"
    assert snap["usage"]["total_tokens"] == 46
    assert snap["usage"]["prompt_tokens"] == 12
    assert snap["usage"]["max_tokens"] == 8192
    assert snap["step"] == "combine"


def test_ask_refuses_when_tutorial_missing(tmp_path: Path):
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    res = client.post("/api/tutorials/NoSuch/ask", json={"question": "入口在哪"})
    assert res.status_code == 400
    assert "还没有生成" in res.json()["detail"]


def test_ask_llm_error_is_readable_400(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n", encoding="utf-8")

    def ask_fn(folder, question):
        raise ValueError("OPENROUTER_API_KEY is not set")

    app = create_app(
        output_dir=tmp_path / "output",
        runner=lambda cmd, on_line, cwd: 0,
        python_exe="python",
        ask_fn=ask_fn,
    )
    client = TestClient(app)
    res = client.post("/api/tutorials/Demo/ask", json={"question": "是什么"})
    assert res.status_code == 400
    assert "OPENROUTER_API_KEY" in res.json()["detail"]


def test_ask_answers_from_existing_tutorial(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n\n*source: src/a.py*\n", encoding="utf-8")

    def ask_fn(folder, question):
        return f"from {folder.name}: {question}"

    app = create_app(output_dir=tmp_path / "output", runner=lambda cmd, on_line, cwd: 0, python_exe="python", ask_fn=ask_fn)
    client = TestClient(app)
    res = client.post("/api/tutorials/Demo/ask", json={"question": "是什么"})
    assert res.status_code == 200
    assert res.json()["answer"] == "from Demo: 是什么"


def test_preview_does_not_start_job(tmp_path: Path):
    src = tmp_path / "code"
    src.mkdir()
    (src / "app.py").write_text("print(1)\n", encoding="utf-8")
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: (_ for _ in ()).throw(AssertionError("preview must not start"))))
    res = client.post(
        "/api/jobs/preview",
        json={
            "source_type": "dir",
            "local_dir": str(src),
            "include": "*.py",
            "max_abstractions": 4,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["dry_run"] is True
    assert data["file_count"] == 1
    assert data["estimated_calls"]["total"] == 7
    snap = client.get("/api/jobs/current").json()
    assert snap["status"] == "idle"


def test_chapter_page_shows_truncation_banners(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n", encoding="utf-8")
    (dest / "01_one.md").write_text(
        "# Chapter 1: One\n\n<!-- qs:truncated_files=3 total=5 -->\n\n正文\n",
        encoding="utf-8",
    )
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    page = client.get("/t/Demo/01_one.md")
    assert page.status_code == 200
    assert "引用文件截断：3 / 5" in page.text
    assert page.text.count("引用文件截断：3 / 5") >= 2
    assert 'id="chapter-nav"' in page.text
    assert "bindMermaidChapterClicks" in page.text


def test_index_mermaid_page_includes_chapter_nav(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text(
        "# Demo\n\n```mermaid\nflowchart TD\n    A0[\"入口\"]\n```\n",
        encoding="utf-8",
    )
    (dest / "01_entry.md").write_text("# Chapter 1: 入口\n\n正文\n", encoding="utf-8")
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    page = client.get("/t/Demo")
    assert page.status_code == 200
    assert 'id="chapter-nav"' in page.text
    assert "01_entry.md" in page.text
    assert "入口" in page.text


def test_second_job_returns_conflict(tmp_path: Path):
    gate = tmp_path / "gate"

    def runner(cmd, on_line, cwd):
        while not gate.exists():
            pass
        return 0

    client = TestClient(_app(tmp_path, runner))
    first = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/other"},
    )
    assert second.status_code == 409
    gate.write_text("ok", encoding="utf-8")
    client.app.state.manager.wait(timeout=5)
