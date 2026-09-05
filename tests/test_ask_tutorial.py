from pathlib import Path

import pytest

from utils.ask_tutorial import (
    AskRefused,
    ask_tutorial,
    ask_tutorial_detailed,
    collect_tutorial_bundle,
    score_chapter,
    select_ask_chapters,
)


def test_ask_refuses_without_tutorial(tmp_path: Path):
    empty = tmp_path / "missing"
    empty.mkdir()
    with pytest.raises(AskRefused, match="还没有生成"):
        ask_tutorial(empty, "入口在哪")


def test_ask_uses_only_tutorial_and_source_paths(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("# Demo\n\n概述\n", encoding="utf-8")
    (folder / "01_entry.md").write_text(
        "# Chapter 1: 入口\n\n*source: src/main.py*\n\n# source: src/app.py\n",
        encoding="utf-8",
    )
    captured = {}

    def fake_llm(prompt, use_cache=True, temperature=0.7):
        captured["prompt"] = prompt
        return "入口在 src/main.py"

    answer = ask_tutorial(folder, "入口在哪？", call=fake_llm)
    assert answer == "入口在 src/main.py"
    assert "src/main.py" in captured["prompt"]
    assert "src/app.py" in captured["prompt"]
    assert "只能根据" in captured["prompt"] or "不要使用教程以外" in captured["prompt"]
    bundle = collect_tutorial_bundle(folder)
    assert "src/main.py" in bundle["sources"]


def test_ask_does_not_embed_entire_book_when_over_limit(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("# Demo\n\n概述\n", encoding="utf-8")
    (folder / "01_entry.md").write_text(
        "# Chapter 1: 入口\n\n*source: src/main.py*\n\n" + ("入口说明。" * 80),
        encoding="utf-8",
    )
    (folder / "02_cache.md").write_text(
        "# Chapter 2: 缓存\n\n*source: src/cache.py*\n\nSECRET_CACHE_BODY\n" + ("缓存说明。" * 80),
        encoding="utf-8",
    )
    (folder / "03_auth.md").write_text(
        "# Chapter 3: 鉴权\n\n*source: src/auth.py*\n\nSECRET_AUTH_BODY\n" + ("鉴权说明。" * 80),
        encoding="utf-8",
    )
    captured = {}

    def fake_llm(prompt, use_cache=True, temperature=0.7):
        captured["prompt"] = prompt
        return "入口在第一章"

    result = ask_tutorial_detailed(
        folder, "入口在 src/main.py 哪里？", call=fake_llm, top_k=1, max_chars=400
    )
    prompt = captured["prompt"]
    assert "全部章节目录" in prompt
    assert "01_entry.md" in prompt
    assert "SECRET_CACHE_BODY" not in prompt
    assert "SECRET_AUTH_BODY" not in prompt
    assert result.routed is True
    assert result.used_chapters
    assert result.used_chapters[0]["filename"] == "01_entry.md"


def test_ask_top_k_prefers_source_path(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("# Demo\n", encoding="utf-8")
    (folder / "01_a.md").write_text("# A\n\n*source: src/other.py*\n", encoding="utf-8")
    (folder / "02_b.md").write_text("# B\n\n*source: src/auth.py*\nAUTH_ONLY\n", encoding="utf-8")
    bundle = collect_tutorial_bundle(folder)
    selected, routed = select_ask_chapters(
        bundle["chapters"], "src/auth.py 怎么工作", top_k=1, max_chars=200
    )
    assert routed is True
    assert selected[0].filename == "02_b.md"
    assert score_chapter(selected[0], "src/auth.py") > score_chapter(
        bundle["chapters"][1], "无关问题 xyz"
    )
