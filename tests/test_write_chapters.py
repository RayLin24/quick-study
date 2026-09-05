import asyncio

import pytest

from nodes import CombineTutorial, WriteChapters, finalize_chapter, inject_truncation_note


def _item():
    return {
        "chapter_num": 1,
        "abstraction_details": {"name": "入口", "description": "desc"},
        "related_files_content_map": {},
        "project_name": "demo",
        "full_chapter_listing": "1. [入口](01_x.md)",
        "full_chapter_outline": "1. [入口](01_x.md)\n   desc",
        "chapter_filenames": {0: {"num": 1, "name": "入口", "filename": "01_x.md"}},
        "prev_chapter": None,
        "next_chapter": None,
        "language": "chinese",
        "use_cache": True,
    }


def test_finalize_chapter_rejects_heading_only():
    with pytest.raises(ValueError, match="too short"):
        finalize_chapter("", chapter_num=1, abstraction_name="入口")


def test_write_chapters_prep_includes_relationship_edges():
    shared = {
        "chapter_order": [0, 1],
        "abstractions": [
            {"name": "入口", "description": "desc A", "files": []},
            {"name": "核心", "description": "desc B", "files": []},
        ],
        "files": [],
        "project_name": "demo",
        "language": "chinese",
        "use_cache": True,
        "relationships": {"details": [{"from": 0, "to": 1, "label": "调用"}]},
    }

    items = asyncio.run(WriteChapters().prep_async(shared))
    assert len(items) == 2
    assert "调用" in items[0]["relationship_edges"]
    assert "01_" in items[0]["relationship_edges"] or "核心" in items[0]["relationship_edges"]
    assert items[1]["relationship_edges"]


def test_finalize_chapter_keeps_real_body():
    body = "# Chapter 1: 入口\n\n" + ("一段说明。" * 40)
    assert finalize_chapter(body, chapter_num=1, abstraction_name="入口").startswith("# Chapter 1:")


def test_write_chapters_retries_skip_cache(monkeypatch):
    calls = []

    def fake_llm(prompt, use_cache=True, progress_label=None):
        calls.append(use_cache)
        if len(calls) == 1:
            return ""
        return "# Chapter 1: 入口\n\n" + ("正文内容。" * 40)

    monkeypatch.setattr("nodes.call_llm", fake_llm)
    node = WriteChapters()
    node.cur_retry = 0
    node._attempts = {}

    async def run():
        node._semaphore = asyncio.Semaphore(1)
        node.cur_retry = 0
        with pytest.raises(ValueError):
            await node.exec_async(_item())
        node.cur_retry = 1
        return await node.exec_async(_item())

    result = asyncio.run(run())
    assert "正文内容" in result
    assert "qs:truncated_files=" in result
    assert calls == [True, False]


def test_combine_mermaid_includes_click_targets():
    shared = {
        "project_name": "Demo",
        "output_dir": "/tmp/out",
        "repo_url": "https://github.com/o/r",
        "relationships": {"summary": "sum", "details": [{"from": 0, "to": 1, "label": "uses"}]},
        "chapter_order": [0, 1],
        "abstractions": [
            {"name": "入口", "description": "d", "files": []},
            {"name": "核心", "description": "d", "files": []},
        ],
        "chapters": [
            "# Chapter 1: 入口\n\n" + ("说明。" * 40),
            "# Chapter 2: 核心\n\n" + ("说明。" * 40),
        ],
        "language": "Chinese",
        "file_count": 2,
        "map_mode": False,
    }
    prep = CombineTutorial().prep(shared)
    assert "```mermaid" in prep["index_content"]
    assert 'click A0 "' in prep["index_content"]
    assert 'click A1 "' in prep["index_content"]
    assert "01_" in prep["index_content"]


def test_inject_truncation_note_after_heading():
    body = "# Chapter 1: 入口\n\n" + ("一段说明。" * 40)
    out = inject_truncation_note(body, {"truncated_files": 2, "file_count": 6})
    assert out.startswith("# Chapter 1:")
    assert "<!-- qs:truncated_files=2 total=6 -->" in out
    assert "引用了 6 个文件" in out
    assert "2 个因长度限制被截断" in out
