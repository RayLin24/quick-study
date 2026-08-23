import asyncio

import pytest

from nodes import WriteChapters, finalize_chapter


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
    assert calls == [True, False]
