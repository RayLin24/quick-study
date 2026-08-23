from pathlib import Path

import pytest

from web.render import list_tutorials, markdown_to_html, resolve_tutorial_file


def test_mermaid_fence_becomes_div():
    html = markdown_to_html(
        "Intro\n\n```mermaid\nflowchart TD\n    A0[\"Node\"]\n```\n\nMore",
        tutorial_name="Demo",
    )
    assert '<div class="mermaid">' in html
    assert "flowchart TD" in html
    assert "```mermaid" not in html
    assert "figure class=\"diagram" in html


def test_article_gets_cover_lead_and_table_wrap():
    html = markdown_to_html(
        "# Chapter 1: Agent\n\nAgent 把模型和工具串起来。\n\n| 方法 | 作用 |\n| --- | --- |\n| invoke | 一次调用 |\n",
        tutorial_name="Demo",
    )
    assert '<figure class="cover">' in html
    assert 'class="lead"' in html
    assert '<div class="table-wrap">' in html
    assert "<table>" in html
    assert "Agent" in html


def test_relative_markdown_links_point_at_tutorial_route():
    html = markdown_to_html("[Ch1](01_agent.md)", tutorial_name="PocketFlow")
    assert 'href="/t/PocketFlow/01_agent.md"' in html


def test_resolve_tutorial_file_blocks_path_escape(tmp_path: Path):
    output = tmp_path / "output"
    tutorial = output / "Demo"
    tutorial.mkdir(parents=True)
    (tutorial / "index.md").write_text("# hi", encoding="utf-8")
    (tmp_path / "secret.md").write_text("nope", encoding="utf-8")

    resolved = resolve_tutorial_file(output, "Demo", "index.md")
    assert resolved == tutorial / "index.md"

    with pytest.raises(ValueError):
        resolve_tutorial_file(output, "Demo", "../secret.md")


def test_list_tutorials_only_includes_index_markdown(tmp_path: Path):
    output = tmp_path / "output"
    (output / "Ready").mkdir(parents=True)
    (output / "Ready" / "index.md").write_text("# r", encoding="utf-8")
    (output / "Empty").mkdir()
    assert list_tutorials(output) == [{"name": "Ready"}]
