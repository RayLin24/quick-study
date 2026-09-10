"""#23 Citation anchor accuracy — mock LLM, assert routing + href shape."""

from __future__ import annotations

from pathlib import Path

from utils.ask_citations import extract_citations, used_chapter_citations
from utils.ask_tutorial import ChapterDoc, ask_tutorial_detailed, select_ask_chapters
from web.render import slugify_heading


def _chapters():
    auth = ChapterDoc(
        "02_auth.md",
        "鉴权中间件",
        "# Chapter 2: 鉴权中间件\n\n## 动机\n\n校验 JWT。\n\n*source: src/auth.py*\n",
        ["src/auth.py"],
        80,
    )
    decoy = ChapterDoc(
        "01_middleware.md",
        "通用中间件",
        "# Chapter 1: 通用中间件\n\n## 日志\n\n只打日志。\n\n*source: src/middleware.py*\n",
        ["src/middleware.py"],
        70,
    )
    return [decoy, auth]


def test_23_routing_picks_auth_not_decoy():
    chapters = _chapters()
    # Current scorer is title + *source:* path (PR #11 BM25 is not on main).
    selected, routed = select_ask_chapters(chapters, "鉴权中间件 src/auth.py", top_k=1, max_chars=20000)
    assert routed
    assert selected[0].filename == "02_auth.md"


def test_23_link_shape_from_answer_and_used():
    chapters = _chapters()
    answer = "见 [鉴权中间件](02_auth.md#动机) 与 02_auth.md"
    cites = extract_citations(answer, chapters, tutorial_name="Demo")
    assert cites
    href = cites[0]["href"]
    assert href.startswith("/t/Demo/02_auth.md")
    assert "#" in href
    used = used_chapter_citations(
        [{"filename": "02_auth.md", "title": "鉴权中间件"}],
        "Demo",
        chapters,
    )
    assert used[0]["href"].startswith("/t/Demo/02_auth.md#")
    want = slugify_heading("动机")
    assert want in used[0]["href"] or used[0]["anchor"]


def test_23_ask_detailed_mock_llm(tmp_path: Path):
    folder = tmp_path / "Demo"
    folder.mkdir()
    (folder / "index.md").write_text("# Demo\n\n总览。\n", encoding="utf-8")
    (folder / "02_auth.md").write_text(
        "# Chapter 2: 鉴权中间件\n\n## 动机\n\n校验 JWT。\n\n*source: src/auth.py*\n",
        encoding="utf-8",
    )
    (folder / "01_middleware.md").write_text(
        "# Chapter 1: 通用中间件\n\n## 日志\n\n只打日志。\n\n*source: src/middleware.py*\n",
        encoding="utf-8",
    )

    def fake(prompt, **_kwargs):
        assert "选中章节" in prompt or "教程" in prompt
        return "入口在 [鉴权中间件](02_auth.md#动机)"

    result = ask_tutorial_detailed(folder, "鉴权中间件 src/auth.py", call=fake, top_k=1)
    assert result.used_chapters[0]["filename"] == "02_auth.md"
    assert result.citations
    assert result.citations[0]["href"].startswith("/t/Demo/02_auth.md")
    assert "#" in result.citations[0]["href"]
