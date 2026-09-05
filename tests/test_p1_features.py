import io
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from utils.allow_dir import assert_allowed_local_dir
from utils.crawl_cache import load_crawl_cache, save_crawl_cache
from utils.dead_links import find_dead_markdown_links
from utils.export_tutorial import build_llms_txt, zip_tutorial
from utils.include_suggest import suggest_include_patterns
from utils.language import DEFAULT_LANGUAGE, normalize_language
from utils.polish import polish_transitions
from utils.redact import redact_text
from utils.relationships_check import append_required_links, relationship_coverage
from utils.resume import load_saved_chapter, save_chapter
from utils.source_format import github_blob_url, unified_source_line
from utils.strategy import apply_strategy, get_strategy
from web.render import list_tutorials, mermaid_click_bindings
from web.tutorial_ops import delete_tutorial, rename_tutorial
from webapp import create_app


def test_default_language_single_source():
    assert DEFAULT_LANGUAGE
    assert normalize_language("") == DEFAULT_LANGUAGE
    assert normalize_language("english") == "english"


def test_strategy_presets():
    beginner = get_strategy("beginner")
    assert beginner["max_abstractions"] == 6
    deep = apply_strategy({"include": ""}, "deep")
    assert deep["max_abstractions"] == 12
    skim = apply_strategy({}, "skim")
    assert skim["overview_only"] is True


def test_allow_dir_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "ok"
    root.mkdir()
    inside = root / "proj"
    inside.mkdir()
    monkeypatch.setenv("ALLOW_DIR_ROOT", str(root))
    assert_allowed_local_dir(inside)
    with pytest.raises(ValueError, match="ALLOW_DIR_ROOT"):
        assert_allowed_local_dir(tmp_path / "other")


def test_crawl_cache_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("utils.crawl_cache.CACHE_DIR", tmp_path / "cc")
    payload = {"local_dir": "/tmp/x", "include": ["*.py"], "max_size": 10}
    save_crawl_cache(payload, [("a.py", "print(1)")])
    loaded = load_crawl_cache(payload, max_age=9999)
    assert loaded == [("a.py", "print(1)")]


def test_resume_saves_and_reloads(tmp_path: Path):
    folder = tmp_path / "Demo"
    text = "# Chapter 1: 入口\n\n" + ("正文内容。" * 40)
    save_chapter(folder, "01_x.md", text)
    assert load_saved_chapter(folder, "01_x.md").startswith("# Chapter 1")


def test_llms_txt_and_zip(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("# Demo\n\n概述项目。\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# Chapter 1: 入口\n\n讲入口。\n", encoding="utf-8")
    txt = build_llms_txt(folder)
    assert "01_a.md" in txt
    assert "入口" in txt
    blob = zip_tutorial(folder)
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        assert "index.md" in zf.namelist()
        assert "01_a.md" in zf.namelist()


def test_dead_links_and_relationship_coverage():
    assert relationship_coverage(3, [{"from": 0, "to": 1}])["orphans"] == [2]
    text = append_required_links("# Hi\n", '- [核心](02_core.md)')
    assert "[核心](02_core.md)" in text


def test_dead_markdown_links(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("[gone](99_missing.md)\n", encoding="utf-8")
    dead = find_dead_markdown_links(folder)
    assert dead and dead[0]["target"] == "99_missing.md"


def test_source_and_redact():
    assert unified_source_line("a.py") == "*source: a.py*"
    assert "blob/HEAD/src/a.py" in (github_blob_url("https://github.com/o/r", "src/a.py") or "")
    assert "***" in redact_text("OPENROUTER_API_KEY=sk-secret")
    assert "sk-secret" not in redact_text("token: abcdefghijklmnop")


def test_include_suggest_and_polish():
    assert "src/**" in suggest_include_patterns([("src/a.py", "x"), ("src/b.py", "y")])
    polished = polish_transitions(["# A\n\nbody", "# B\n\nbody"])
    assert "承接上一章" in polished[1]


def test_list_tutorials_sorts_mtime(tmp_path: Path):
    output = tmp_path / "output"
    old = output / "Old"
    new = output / "New"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "index.md").write_text("# old\n", encoding="utf-8")
    (new / "index.md").write_text("# new\n", encoding="utf-8")
    import time
    (old / "index.md").touch()
    time.sleep(0.05)
    (new / "index.md").touch()
    names = [item["name"] for item in list_tutorials(output)]
    assert names[0] == "New"


def test_delete_rename_and_exports_http(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n\n概述\n", encoding="utf-8")
    (dest / "01_a.md").write_text("# A\n\n*source: src/a.py*\n", encoding="utf-8")
    (dest / "meta.json").write_text('{"language":"Chinese","repo_url":"https://github.com/o/r"}', encoding="utf-8")
    app = create_app(output_dir=tmp_path / "output", runner=lambda cmd, on_line, cwd: 0, python_exe="python")
    client = TestClient(app)
    llms = client.get("/api/tutorials/Demo/llms.txt")
    assert llms.status_code == 200
    assert "Demo" in llms.text
    zipped = client.get("/api/tutorials/Demo/export.zip")
    assert zipped.status_code == 200
    assert zipped.headers["content-type"].startswith("application/zip")
    cleared = client.post("/api/tutorials/Demo/cache/clear")
    assert cleared.status_code == 200
    renamed = client.post("/api/tutorials/Demo/rename", json={"name": "Demo2"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "Demo2"
    gone = client.delete("/api/tutorials/../secret")
    assert gone.status_code in (400, 404)
    deleted = client.delete("/api/tutorials/Demo2")
    assert deleted.status_code == 200
    assert not (tmp_path / "output" / "Demo2").exists()


def test_replace_job_cancels_then_starts(tmp_path: Path):
    gate = tmp_path / "gate"
    started = {"n": 0}

    def runner(cmd, on_line, cwd, job=None):
        started["n"] += 1
        if started["n"] == 1:
            on_line("first")
            while not gate.exists():
                if job is not None and getattr(job, "cancelled", False):
                    from web.job import JobCancelled
                    raise JobCancelled("任务已取消")
        return 0

    app = create_app(output_dir=tmp_path / "output", runner=runner, python_exe="python")
    client = TestClient(app)
    first = client.post("/api/jobs", json={"source_type": "repo", "repo_url": "https://github.com/o/a"})
    assert first.status_code == 200
    second = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/o/b", "replace": True},
    )
    assert second.status_code == 200
    gate.write_text("ok", encoding="utf-8")
    client.app.state.manager.wait(timeout=5)
    assert started["n"] >= 2


def test_mermaid_bindings_and_chapter_page_source(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n\n```mermaid\nflowchart TD\n    A0[\"入口\"]\n```\n", encoding="utf-8")
    (dest / "01_a.md").write_text("# Chapter 1: 入口\n\n*source: src/a.py*\n", encoding="utf-8")
    (dest / "meta.json").write_text('{"repo_url":"https://github.com/o/r"}', encoding="utf-8")
    client = TestClient(create_app(output_dir=tmp_path / "output", runner=lambda c, o, w: 0, python_exe="python"))
    page = client.get("/t/Demo/01_a.md")
    assert "github.com/o/r/blob/HEAD" in page.text or "src/a.py" in page.text
    mapping = mermaid_click_bindings(
        [{"title": "入口", "href": "/t/Demo/01_a.md", "filename": "01_a.md"}]
    )
    assert mapping["入口"] == "/t/Demo/01_a.md"
