import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils.ask_citations import extract_citations, used_chapter_citations
from utils.ask_tutorial import AskResult, ChapterDoc, ask_tutorial_detailed
from utils.budget import BudgetExceeded, assert_budget, record_tokens
from utils.commit_range import commit_range_guide
from utils.editor_open import resolve_editor_url, vscode_file_url
from utils.graph_color import color_mermaid
from utils.i18n import catalog, normalize_ui_lang, t
from utils.job_control import is_paused, request_pause, request_resume
from utils.job_history import append_history, list_history
from utils.job_queue import dequeue, enqueue, load_queue
from utils.learn_outcomes import inject_outcomes, learning_outcomes
from utils.local_watch import save_watch_snapshot, snapshot_local_dir, watch_status
from utils.next_chapter import recommend_next
from utils.provider_cost import estimate_cost, estimate_from_usage, estimate_preview_calls
from utils.quality_score import score_tutorial
from utils.repo_diff_guide import compare_repos
from utils.retry_chapter import failed_chapters, mark_chapter_for_retry
from web.render import markdown_to_html
from webapp import create_app


def _tutorial(tmp_path: Path, name="Demo"):
    folder = tmp_path / "output" / name
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n概述入口。\n", encoding="utf-8")
    (folder / "01_a.md").write_text(
        "# Chapter 1: 入口\n\n## 动机\n\n入口负责启动。*source: src/main.py*\n\n一段说明。\n",
        encoding="utf-8",
    )
    (folder / "02_b.md").write_text(
        "# Chapter 2: 缓存\n\n*source: src/cache.py*\n\n缓存与入口 src/main.py 协同。\n",
        encoding="utf-8",
    )
    (folder / "meta.json").write_text(
        json.dumps({"language": "Chinese", "repo_url": "https://github.com/acme/demo", "name": name}),
        encoding="utf-8",
    )
    return folder


def _client(tmp_path: Path, **kwargs):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return TestClient(
        create_app(
            output_dir=output,
            runner=kwargs.get("runner") or (lambda cmd, on_line, cwd: 0),
            python_exe="python",
            ask_fn=kwargs.get("ask_fn"),
        )
    )


def test_01_ask_citations_jump_to_anchor(tmp_path: Path):
    folder = _tutorial(tmp_path)
    chapters = [
        ChapterDoc("01_a.md", "入口", (folder / "01_a.md").read_text(encoding="utf-8"), ["src/main.py"], 20)
    ]
    cites = extract_citations("见 [入口](01_a.md#动机)", chapters, tutorial_name="Demo")
    assert cites[0]["filename"] == "01_a.md"
    assert cites[0]["href"].startswith("/t/Demo/01_a.md")
    used = used_chapter_citations([{"filename": "01_a.md", "title": "入口"}], "Demo", chapters)
    assert "#动机" in used[0]["href"] or used[0]["href"].endswith("01_a.md") or "#" in used[0]["href"]
    client = _client(tmp_path)

    def ask_fn(folder, question):
        return AskResult(
            answer="入口在 [入口](01_a.md)",
            used_chapters=[{"filename": "01_a.md", "title": "入口"}],
            citations=[{"filename": "01_a.md", "title": "入口", "href": "/t/Demo/01_a.md#动机-xxxx", "anchor": "x"}],
            markdown="入口在 [入口](01_a.md)",
        )

    app = create_app(output_dir=tmp_path / "output", runner=lambda c, o, w: 0, python_exe="python", ask_fn=ask_fn)
    res = TestClient(app).post("/api/tutorials/Demo/ask", json={"question": "入口"})
    assert res.status_code == 200
    assert res.json()["citations"][0]["href"].startswith("/t/Demo/")
    page = client.get("/t/Demo/01_a.md")
    assert 'id="ask-used-list"' in page.text


def test_02_ask_copy_markdown(tmp_path: Path):
    _tutorial(tmp_path)

    def ask_fn(folder, question):
        return AskResult(answer="**粗体**", markdown="**粗体**")

    res = TestClient(
        create_app(output_dir=tmp_path / "output", runner=lambda c, o, w: 0, python_exe="python", ask_fn=ask_fn)
    ).post("/api/tutorials/Demo/ask", json={"question": "q"})
    assert res.json()["markdown"] == "**粗体**"
    html = Path("web/templates/tutorial.html").read_text(encoding="utf-8")
    assert "ask-copy-md" in html


def test_03_ask_degrade_shorter_context(tmp_path: Path):
    folder = _tutorial(tmp_path)
    calls = {"n": 0}

    def flaky(prompt, use_cache=False, temperature=0.2, stage="ask"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("too long")
        return "短答案"

    result = ask_tutorial_detailed(folder, "入口在哪", call=flaky, max_chars=400)
    assert result.answer == "短答案"
    assert result.degraded is True
    assert result.attempts == 2


def test_04_job_queue_persists(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    gate = tmp_path / "gate"

    def runner(cmd, on_line, cwd, job=None):
        on_line("working")
        while not gate.exists():
            if job is not None and getattr(job, "cancelled", False):
                from web.job import JobCancelled

                raise JobCancelled("任务已取消")
        return 0

    client = TestClient(create_app(output_dir=output, runner=runner, python_exe="python"))
    first = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/a"})
    assert first.status_code == 200
    queued = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/o/b", "queue": True},
    )
    assert queued.status_code == 200
    assert queued.json()["queued"] is True
    listed = client.get("/api/jobs/queue").json()
    assert listed["items"]
    item = enqueue(output, {"source_type": "repo", "repo_url": "https://github.com/o/c"})
    assert item["id"]
    assert load_queue(output)
    gate.write_text("ok", encoding="utf-8")
    client.app.state.manager.wait(timeout=5)


def test_05_job_history_page(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    rec = append_history(
        output,
        {"id": "abc", "status": "succeeded", "output_name": "Demo", "usage": {"total_tokens": 10, "prompt_tokens": 6, "completion_tokens": 4}},
        started_at=1000,
    )
    assert rec["cost"]["usd"] >= 0
    assert list_history(output)[0]["id"] == "abc"
    client = _client(tmp_path)
    res = client.get("/api/jobs/history")
    assert res.status_code == 200
    page = client.get("/jobs")
    assert page.status_code == 200
    assert "任务历史" in page.text


def test_06_budget_circuit_breaker(tmp_path: Path, monkeypatch):
    output = tmp_path / "output"
    output.mkdir()
    monkeypatch.setenv("TOKEN_BUDGET_SESSION", "10")
    record_tokens(output, 10)
    with pytest.raises(BudgetExceeded):
        assert_budget(output, upcoming=1)
    client = _client(tmp_path)
    monkeypatch.setenv("TOKEN_BUDGET_SESSION", "1")
    record_tokens(tmp_path / "output", 5)
    res = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert res.status_code == 429


def test_07_provider_cost_estimate():
    cost = estimate_cost(prompt_tokens=1_000_000, completion_tokens=1_000_000, provider="OPENAI")
    assert cost["usd"] > 1
    assert cost["provider"] == "OPENAI"
    preview = estimate_preview_calls(8)
    assert preview["estimated"] is True
    assert estimate_from_usage({"prompt_tokens": 100, "completion_tokens": 20})["usd"] >= 0


def test_08_quality_score(tmp_path: Path):
    folder = _tutorial(tmp_path)
    (folder / "03_empty.md").write_text("# Empty\n\n", encoding="utf-8")
    (folder / "index.md").write_text("# Demo\n\n[gone](99_missing.md)\n", encoding="utf-8")
    result = score_tutorial(folder)
    assert result["score"] < 100
    assert result["empty_chapters"] or result["dead_links"] or result["missing_source"]
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/quality")
    assert res.status_code == 200
    assert "score" in res.json()


def test_09_learning_outcomes():
    text = "# Chapter 1: 入口\n\n入口负责启动服务。它读取配置。然后监听端口。\n"
    items = learning_outcomes(text)
    assert len(items) == 3
    injected = inject_outcomes(text)
    assert "你将学到" in injected


def test_10_smart_next_chapter(tmp_path: Path):
    folder = _tutorial(tmp_path)
    rec = recommend_next(folder, "01_a.md")
    assert rec["recommended"]["filename"] in {"02_b.md"}
    assert rec["recommended"]["reason"]
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/next", params={"filename": "01_a.md"})
    assert res.status_code == 200
    page = client.get("/t/Demo/01_a.md")
    assert "下一步" in page.text


def test_11_compare_two_repos(tmp_path: Path):
    a = _tutorial(tmp_path, "A")
    b = tmp_path / "output" / "B"
    b.mkdir()
    (b / "index.md").write_text("# B\n", encoding="utf-8")
    (b / "01_x.md").write_text("# X\n\n*source: src/other.py*\n", encoding="utf-8")
    out = compare_repos(a, b, left_name="A", right_name="B")
    assert "src/main.py" in out["only_left_sources"]
    client = _client(tmp_path)
    res = client.post("/api/compare-repos", json={"left": "A", "right": "B"})
    assert res.status_code == 200
    assert res.json()["only_left_sources"]


def test_12_commit_range_guide(tmp_path: Path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@e.st"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.py").write_text("print(1)\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, check=True, capture_output=True)
    first = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    (repo / "b.py").write_text("print(2)\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=repo, check=True, capture_output=True)
    guide = commit_range_guide(repo, first, "HEAD")
    assert "b.py" in guide["files"]
    client = _client(tmp_path)
    res = client.post("/api/guides/commits", json={"local_dir": str(repo), "since": first, "until": "HEAD"})
    assert res.status_code == 200
    assert res.json()["files"]


def test_13_graph_color_by_lang():
    src = 'flowchart TD\n    A0["src/main.py"]\n    A1["web/app.js"]\n'
    colored = color_mermaid(src, ["src/main.py", "web/app.js"], by="lang")
    assert "classDef" in colored
    assert "class A0" in colored
    client_src = Path("webapp.py").read_text(encoding="utf-8")
    assert "/api/graph/color" in client_src


def test_14_mermaid_zoom_markup(tmp_path: Path):
    html = markdown_to_html("```mermaid\nflowchart TD\n A[x]\n```\n", "Demo")
    assert "mermaid-zoom" in html
    assert "mermaid-scroller" in html
    js = Path("web/static/reader.js").read_text(encoding="utf-8")
    assert "mermaid-zoom-in" in js


def test_15_open_in_editor():
    url = vscode_file_url("/tmp/proj/src/a.py", line=3)
    assert url.startswith("vscode://file/")
    assert url.endswith(":3")
    info = resolve_editor_url("src/a.py", local_dir="/tmp/proj")
    assert "vscode://file" in info["vscode"]
    html = markdown_to_html("*source: src/a.py*\n\ncode\n", "Demo")
    assert "open-editor" in html
    assert "vscode://file" in html


def test_16_local_watch(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    folder = _tutorial(tmp_path)
    meta = json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    meta["local_dir"] = str(src)
    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    snap = snapshot_local_dir(src)
    save_watch_snapshot(folder, [{"rel": "a.py", "mtime": 1, "size": 1}])
    status = watch_status(folder, src)
    assert status["needs_incremental"] is True
    client = _client(tmp_path)
    res = client.get("/api/tutorials/Demo/watch")
    assert res.status_code == 200


def test_17_pause_and_resume(tmp_path: Path):
    output = tmp_path / "output"
    output.mkdir()
    request_pause(output)
    assert is_paused(output)
    request_resume(output)
    assert not is_paused(output)
    running = {"go": False}

    def runner(cmd, on_line, cwd, job=None):
        on_line("working")
        while not running["go"]:
            pass
        return 0

    client = TestClient(create_app(output_dir=output, runner=runner, python_exe="python"))
    started = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/r"})
    assert started.status_code == 200
    paused = client.post("/api/jobs/current/pause")
    assert paused.status_code == 200
    assert paused.json()["paused"] is True
    resumed = client.post("/api/jobs/current/resume")
    assert resumed.status_code == 200
    assert resumed.json()["paused"] is False
    running["go"] = True
    client.app.state.manager.wait(timeout=5)


def test_18_retry_failed_chapter(tmp_path: Path):
    folder = _tutorial(tmp_path)
    (folder / "03_fail.md").write_text("# Fail\n\nx\n", encoding="utf-8")
    items = failed_chapters(folder)
    assert any(i["filename"] == "03_fail.md" for i in items)
    marked = mark_chapter_for_retry(folder, "03_fail.md")
    assert marked["cleared"] is True
    assert not (folder / "03_fail.md").exists()
    client = _client(tmp_path)
    listed = client.get("/api/tutorials/Demo/failed-chapters")
    assert listed.status_code == 200


def test_19_i18n_toggle():
    assert normalize_ui_lang("english") == "en"
    assert t("en", "generate") == "Generate tutorial"
    assert catalog("zh")["strings"]["ask"]
    html = Path("web/templates/index.html").read_text(encoding="utf-8")
    assert "lang-toggle" in html


def test_20_a11y_reader_controls():
    js = Path("web/static/reader.js").read_text(encoding="utf-8")
    css = Path("web/static/app.css").read_text(encoding="utf-8")
    html = Path("web/templates/tutorial.html").read_text(encoding="utf-8")
    assert "a11y-size" in js and "a11y-leading" in js and "a11y-contrast" in js
    assert "a11y-lg" in css and "a11y-hc" in css
    assert "a11y-bar" in html
