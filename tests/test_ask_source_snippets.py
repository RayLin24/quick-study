"""#8 Optional Ask source snippets (default OFF). crawl_cache / local, ≤N lines."""

from pathlib import Path

from fastapi.testclient import TestClient

from utils.ask_source_snippets import (
    LAYER_SOURCE,
    LAYER_TUTORIAL,
    best_line_window,
    collect_source_snippets,
    resolve_source_text,
    source_snippets_enabled,
)
from utils.ask_tutorial import AskResult, ask_tutorial_detailed, collect_tutorial_bundle
from utils.crawl_cache import save_crawl_cache
from webapp import create_app


def _tutorial(folder: Path, *, local_dir: str | None = None) -> Path:
    folder.mkdir(parents=True)
    (folder / "index.md").write_text("# Demo\n\n概述\n", encoding="utf-8")
    (folder / "01_entry.md").write_text(
        "# Chapter 1: 入口\n\n*source: src/main.py*\n\n入口说明。\n",
        encoding="utf-8",
    )
    meta = {"name": "Demo", "language": "Chinese"}
    if local_dir:
        meta["local_dir"] = local_dir
    import json

    (folder / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return folder


def test_source_snippets_env_default_off(monkeypatch):
    monkeypatch.delenv("ASK_SOURCE_SNIPPETS", raising=False)
    assert source_snippets_enabled() is False
    assert source_snippets_enabled(False) is False
    assert source_snippets_enabled(True) is True
    monkeypatch.setenv("ASK_SOURCE_SNIPPETS", "1")
    assert source_snippets_enabled() is True
    assert source_snippets_enabled(False) is False


def test_ask_does_not_include_source_by_default(tmp_path: Path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "src").mkdir()
    (src / "src" / "main.py").write_text("SECRET_SOURCE_BODY = 1\n", encoding="utf-8")
    folder = _tutorial(tmp_path / "Demo", local_dir=str(src))
    captured = {}

    def fake_llm(prompt, use_cache=True, temperature=0.7, stage="ask"):
        captured["prompt"] = prompt
        return "入口在第一章"

    result = ask_tutorial_detailed(folder, "入口在哪", call=fake_llm)
    assert result.include_source is False
    assert result.source_snippets == []
    assert result.evidence["layers"] == [LAYER_TUTORIAL]
    assert "SECRET_SOURCE_BODY" not in captured["prompt"]
    assert "未启用（默认关闭）" in captured["prompt"]
    assert "证据层 · 教程" in captured["prompt"]


def test_ask_source_snippets_from_local_dir(tmp_path: Path):
    src = tmp_path / "repo"
    (src / "src").mkdir(parents=True)
    lines = ["# main"] + [f"line_{i} = {i}" for i in range(80)]
    lines[12] = "def boot():  # 入口 startup"
    (src / "src" / "main.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    folder = _tutorial(tmp_path / "Demo", local_dir=str(src))
    captured = {}

    def fake_llm(prompt, use_cache=True, temperature=0.7, stage="ask"):
        captured["prompt"] = prompt
        return "见 main.py"

    result = ask_tutorial_detailed(
        folder, "入口 startup 在哪", call=fake_llm, include_source=True, max_snippet_lines=10
    )
    assert result.include_source is True
    assert result.source_snippets
    snippet = result.source_snippets[0]
    assert snippet["path"] == "src/main.py"
    assert snippet["layer"] == LAYER_SOURCE
    assert snippet["lines"] <= 10
    assert "boot" in snippet["text"]
    assert "证据层 · 源码" in captured["prompt"]
    assert "src/main.py" in captured["prompt"]
    assert result.evidence["source"][0]["path"] == "src/main.py"
    assert LAYER_SOURCE in result.evidence["layers"]


def test_ask_source_snippets_from_crawl_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("utils.crawl_cache.CACHE_DIR", tmp_path / "cc")
    monkeypatch.setattr("utils.ask_source_snippets.load_cached_file_map", lambda max_age=None: {
        "src/main.py": "def entry():\n    return 'CRAWL_CACHE_HIT'\n"
    })
    folder = _tutorial(tmp_path / "Demo")
    captured = {}

    def fake_llm(prompt, use_cache=True, temperature=0.7, stage="ask"):
        captured["prompt"] = prompt
        return "cache"

    result = ask_tutorial_detailed(folder, "entry", call=fake_llm, include_source=True, max_snippet_lines=8)
    assert any("CRAWL_CACHE_HIT" in (sn.get("text") or "") for sn in result.source_snippets)
    assert "CRAWL_CACHE_HIT" in captured["prompt"]


def test_collect_snippets_uses_saved_crawl_cache(tmp_path: Path, monkeypatch):
    cache_dir = tmp_path / "cc"
    monkeypatch.setattr("utils.crawl_cache.CACHE_DIR", cache_dir)
    save_crawl_cache(
        {"local_dir": str(tmp_path / "other"), "include": ["*.py"], "max_size": 99},
        [("src/main.py", "print('from-cache')\n")],
    )
    folder = _tutorial(tmp_path / "Demo")
    bundle = collect_tutorial_bundle(folder)
    items = collect_source_snippets(bundle["chapters"], "print", folder=folder, max_lines=5)
    assert items
    assert "from-cache" in items[0]["text"]


def test_snippet_line_cap_and_path_escape(tmp_path: Path):
    src = tmp_path / "repo"
    (src / "src").mkdir(parents=True)
    (src / "src" / "main.py").write_text("\n".join(f"n{i}" for i in range(120)) + "\n", encoding="utf-8")
    (src / "secret.txt").write_text("NOPE\n", encoding="utf-8")
    start, window = best_line_window((src / "src" / "main.py").read_text(encoding="utf-8"), "n10", 7)
    assert len(window) == 7
    assert start >= 1
    assert resolve_source_text("../secret.txt", local_dir=src) is None
    assert resolve_source_text("/etc/passwd", local_dir=src) is None
    assert resolve_source_text("src/main.py", local_dir=src)


def test_tutorial_page_labels_evidence_layers():
    html = Path("web/templates/tutorial.html").read_text(encoding="utf-8")
    assert "evidence-tutorial" in html
    assert "evidence-source" in html
    assert "ask-include-source" in html
    assert "默认关闭" in html
    assert 'id="ask-source-list"' in html
    assert 'id="ask-used-list"' in html


def test_ask_api_include_source_default_off(tmp_path: Path):
    dest = tmp_path / "output" / "Demo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# Demo\n", encoding="utf-8")
    seen = {}

    def ask_fn(folder, question, include_source=False):
        seen["include_source"] = include_source
        return AskResult(answer="ok", include_source=include_source)

    app = create_app(
        output_dir=tmp_path / "output",
        runner=lambda cmd, on_line, cwd: 0,
        python_exe="python",
        ask_fn=ask_fn,
    )
    client = TestClient(app)
    res = client.post("/api/tutorials/Demo/ask", json={"question": "入口"})
    assert res.status_code == 200
    assert seen["include_source"] is False
    assert res.json()["include_source"] is False
    on = client.post("/api/tutorials/Demo/ask", json={"question": "入口", "include_source": True})
    assert on.status_code == 200
    assert seen["include_source"] is True
