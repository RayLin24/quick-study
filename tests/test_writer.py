"""M4 单测：上下文包预算/去重、质检关卡、L3 覆盖率、VitePress 组装（不依赖 LLM/Docker）。"""
import json

from quickstudy.storage import Workspace
from quickstudy.writer.assemble import assemble_book
from quickstudy.writer.chapter import (build_chapter_context, chapter_filename,
                                       estimate_chapter_costs, extract_chapter_summary)
from quickstudy.writer.qc import check_chapter, l3_coverage


def _ws_with_chunks(tmp_path):
    ws = Workspace(tmp_path / "task")
    chunks = [
        {"chunk_id": "aaa1", "text": "FastAPI is a modern web framework. " * 10,
         "title": "First Steps", "section_path": "Tutorial", "url": "https://x.com/a"},
        {"chunk_id": "bbb2", "text": "Path parameters with type annotations. " * 10,
         "title": "Path Params", "section_path": "Tutorial", "url": "https://x.com/a"},
        {"chunk_id": "ccc3", "text": "duplicated content",
         "title": "Dup", "section_path": "", "url": "https://x.com/b",
         "duplicate_of": "aaa1"},
    ]
    (ws.path("chunks", "pa.jsonl")).write_text(
        "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks), encoding="utf-8")
    (ws.path("chunks", "pb.jsonl")).write_text(json.dumps(
        {"chunk_id": "ddd4", "text": "Dependency injection system. " * 10,
         "title": "DI", "section_path": "Advanced", "url": "https://x.com/b"},
        ensure_ascii=False), encoding="utf-8")
    ws.write_json("manifest.json", {"pages": {
        "https://x.com/a": {"parsed": True, "page_id": "pa", "sidebar_index": 0},
        "https://x.com/b": {"parsed": True, "page_id": "pb", "sidebar_index": 1},
        "https://x.com/c": {"parsed": True, "page_id": "pc", "sidebar_index": 2},
    }})
    return ws


_CONCEPTS = [{"name": "入门", "pages": ["pa"], "chunks": ["aaa1", "bbb2", "ccc3"]},
             {"name": "依赖注入", "pages": ["pb"], "chunks": ["ddd4"]}]
_CHAPTER = {"no": 1, "title": "快速上手", "concept_ids": [0], "difficulty": 1,
            "est_hours": 1.0, "summary": "10 分钟跑起来"}


def test_context_dedup_and_order(tmp_path):
    ws = _ws_with_chunks(tmp_path)
    context, included, dropped = build_chapter_context(ws, _CHAPTER, [_CONCEPTS[0]])
    assert included == ["aaa1", "bbb2"]     # ccc3 是 duplicate_of，被跳过
    assert dropped == []
    assert "https://x.com/a" in context and "chunk aaa1" in context


def test_context_full_fidelity(tmp_path):
    ws = _ws_with_chunks(tmp_path)
    context, included, dropped = build_chapter_context(ws, _CHAPTER, [_CONCEPTS[0]])
    assert dropped == []                              # 无预算截断，全量进入
    long_chunk = "x" * 5000
    (ws.path("chunks", "pa.jsonl")).write_text(
        json.dumps({"chunk_id": "eee5", "text": long_chunk, "title": "Long",
                    "section_path": "", "url": "https://x.com/a"}), encoding="utf-8")
    context2, _, _ = build_chapter_context(
        ws, {"concept_ids": [0]},
        [{"name": "长文", "pages": ["pa"], "chunks": ["eee5"]}])
    assert long_chunk in context2                     # 单 chunk 也不截断


def test_estimate_costs(tmp_path):
    ws = _ws_with_chunks(tmp_path)
    outline = {"book_title": "测试手册", "chapters": [_CHAPTER]}
    graph = {"concepts": _CONCEPTS}
    est = estimate_chapter_costs(ws, outline, graph)
    assert est["per_chapter"][0]["context_chunks"] == 2
    assert est["total_est_input"] > 0 and est["total_est_output"] == 5000


_GOOD_MD = """# 第1章 快速上手
> 目标 / 前置 / 难度 / 用时

## 1. 是什么、为什么
FastAPI 是现代 Web 框架（Web Framework）。

## 2. 核心概念拆解
路径参数（Path Parameters）。

## 3. 动手试一试
```python
print("hi")
```

## 4. 原理讲解
它为什么能跑。

## 5. 常见坑与 FAQ
注意类型注解。

## 6. 小结与延伸阅读
本章讲了 FastAPI 入门。

- [First Steps](https://x.com/a)

<!-- chunks: aaa1, bbb2 -->
"""


def test_check_chapter_pass(tmp_path):
    issues = check_chapter(
        _GOOD_MD, context_chunks=["aaa1", "bbb2"], used_chunks=["aaa1", "bbb2"],
        glossary_subset={"FastAPI": {"translation": "", "keep_english": True}})
    assert issues == []


def test_check_chapter_structure_and_placeholder():
    bad = "## 1. 是什么\nTODO: 待补充\n"
    issues = check_chapter(bad, context_chunks=["aaa1"], used_chunks=[],
                           glossary_subset={})
    kinds = {(i["level"], i["kind"]) for i in issues}
    assert ("error", "structure") in kinds
    assert ("error", "placeholder") in kinds
    assert ("error", "trace") in kinds      # 无链接且无 chunks 注释


def test_check_chapter_glossary():
    # keep_english：译名出现而英文原形缺席 → 疑被翻译（warn）
    md = _GOOD_MD.replace("FastAPI", "快速API")
    issues = check_chapter(md, context_chunks=["aaa1", "bbb2"],
                           used_chunks=["aaa1"],
                           glossary_subset={"FastAPI": {"translation": "快速API",
                                                        "keep_english": True}})
    assert any("疑被翻译" in i["detail"] for i in issues)
    # 术语仅出现在上下文而正文未讨论 → 不是违规（原误报大户）
    issues = check_chapter(_GOOD_MD, context_chunks=["aaa1", "bbb2"],
                           used_chunks=["aaa1"],
                           glossary_subset={"WebSockets": {"translation": "",
                                                           "keep_english": True}})
    assert not any(i["kind"] == "glossary" for i in issues)
    # used_chunks 缺少 bbb2 不算错（⊆ 上下文包即可）；超出才算
    issues2 = check_chapter(_GOOD_MD, context_chunks=["aaa1"], used_chunks=["aaa1", "zzz9"],
                            glossary_subset={})
    assert any(i["kind"] == "trace" and i["level"] == "warn" for i in issues2)


def test_extract_summary():
    s = extract_chapter_summary(_GOOD_MD, "fallback")
    assert "FastAPI 入门" in s
    assert extract_chapter_summary("无小结", "fallback") == "fallback"


def test_l3_coverage(tmp_path):
    ws = _ws_with_chunks(tmp_path)
    l3 = l3_coverage(ws, [{"used_chunks": ["aaa1", "bbb2"]}, {"used_chunks": ["ddd4"]}])
    assert l3["covered_pages"] == 2 and l3["total_pages"] == 3
    assert l3["coverage_l3"] == round(2 / 3, 4)
    assert l3["uncovered_urls"] == ["https://x.com/c"]


def test_assemble_book(tmp_path):
    ws = _ws_with_chunks(tmp_path)
    ws.write_json("glossary.json", {"terms": {
        "FastAPI": {"translation": "", "keep_english": True, "note": ""}}})
    ws.write_text("chapters/01-快速上手.md", _GOOD_MD)
    outline = {"book_title": "FastAPI 实战入门", "chapters": [_CHAPTER]}
    out = assemble_book(ws, outline,
                        [{"chapter": _CHAPTER, "filename": "01-快速上手.md"}],
                        [{"no": 1, "title": "快速上手", "filename": "01-快速上手.md"}])
    book = ws.path("output", "book")
    assert (book / "index.md").exists()
    assert (book / "chapters" / "01-快速上手.md").exists()
    assert (book / "appendix" / "glossary.md").read_text(encoding="utf-8").count("FastAPI") >= 1
    cfg = (book / ".vitepress" / "config.mts").read_text(encoding="utf-8")
    assert "/chapters/01-快速上手" in cfg and "FastAPI 实战入门" in cfg
    assert (book / "package.json").exists()
    assert len(out["files"]) == 5 + 1


def test_chapter_filename():
    assert chapter_filename({"no": 3, "title": "依赖注入：从入门到精通"}) == \
        "03-依赖注入-从入门到精通.md"
