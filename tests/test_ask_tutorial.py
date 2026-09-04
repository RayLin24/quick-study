from pathlib import Path

import pytest

from utils.ask_tutorial import AskRefused, ask_tutorial, collect_tutorial_bundle


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
