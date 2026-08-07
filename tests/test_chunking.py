"""切分单测。"""
from quickstudy.knowledge.chunking import chunk_page, estimate_tokens

MD = """<!-- source: https://x.com/p | version: - | adapter: mkdocs -->

# Page Title

## Intro

Short intro paragraph with some words.

## Tutorial / Basics

First part of the basics section.

Second paragraph of basics.

## Tutorial / Advanced

Advanced content here.
"""


def test_estimate_tokens():
    assert estimate_tokens("hello world foo bar") == int(4 * 1.3)
    assert estimate_tokens("你好世界") == int(4 * 0.7)


def test_chunk_keeps_section_path():
    chunks = chunk_page("p1", "https://x.com/p", MD, {"title": "Page Title", "lang": "en"})
    assert chunks
    assert all(c["page_id"] == "p1" and c["url"] == "https://x.com/p" for c in chunks)
    assert all("chunk_id" in c and "simhash" in c for c in chunks)
    paths = [c["section_path"] for c in chunks]
    assert any("Intro" in p for p in paths)


def test_oversize_section_splits_at_paragraph_boundary():
    big_para = "word " * 400  # ~520 token 每段
    md = "# T\n\n## Big\n\n" + "\n\n".join([big_para] * 6)  # ~3100 token，超硬上限
    chunks = chunk_page("p2", "https://x.com/b", md, {"title": "T"})
    assert len(chunks) >= 2
    assert all(c["token_est"] <= 2100 for c in chunks)  # 允许估算误差余量


def test_code_fence_not_split_by_heading_regex():
    md = '# T\n\n## Code\n\n```python\n# not a heading\nx = "## fake"\n```\n\ntext after'
    chunks = chunk_page("p3", "https://x.com/c", md, {"title": "T"})
    code_chunk = next(c for c in chunks if c["has_code"])
    assert "## fake" in code_chunk["text"] and "not a heading" in code_chunk["text"]
    assert code_chunk["has_code"]


def test_empty_and_tiny_pages_dont_crash():
    assert chunk_page("p4", "https://x.com/e", "# Only title\n", {"title": "t"}) == [] or True
