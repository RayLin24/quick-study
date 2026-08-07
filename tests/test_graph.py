"""图谱/索引/术语表单测：LLM 用假网关，embedding 用假向量，不接外网。"""
import json

import pytest

from quickstudy.knowledge.glossary import collect_term_candidates
from quickstudy.knowledge.graph import build_graph, build_page_edges
from quickstudy.knowledge.index import ChunkIndex, FakeEmbedder
from quickstudy.llm.gateway import LLMResponse, extract_json
from quickstudy.storage import Workspace


class FakeLLM:
    """按 prompt_name 返回罐装 JSON 的假网关。"""

    def __init__(self):
        self.calls = []

    async def complete(self, prompt_name, version, system, user, **kw):
        self.calls.append((prompt_name, version))
        if prompt_name == "concepts":
            return LLMResponse(text=json.dumps({"concepts": [
                {"name": "路径参数（Path Parameters）", "description": "从 URL 路径取参", "pages": [0, 1]},
                {"name": "依赖注入（Dependency Injection）", "description": "声明式依赖解析", "pages": [1]},
            ]}), model="fake")
        if prompt_name == "relations":
            return LLMResponse(text=json.dumps({"edges": [
                {"from": 0, "to": 1, "reason": "依赖注入常用在路径操作里"},
                {"from": 1, "to": 99, "reason": "越界应被丢弃"},
            ]}), model="fake")
        if prompt_name == "glossary":
            return LLMResponse(text=json.dumps({"terms": [
                {"term": "FastAPI", "translation": "FastAPI", "keep_english": True, "note": ""},
            ]}), model="fake")
        raise AssertionError(f"unexpected prompt {prompt_name}")


def _make_workspace(tmp_path):
    ws = Workspace(tmp_path / "task")
    manifest = {"pages": {
        "https://x.com/a": {"parsed": True, "page_id": "pa", "sidebar_index": 0, "version": ""},
        "https://x.com/b": {"parsed": True, "page_id": "pb", "sidebar_index": 1, "version": ""},
    }}
    ws.write_json("manifest.json", manifest)
    ws.write_json("parsed/pa.json", {"id": "pa", "url": "https://x.com/a", "ok": True,
                                     "title": "Path Parameters", "lang": "en",
                                     "links": [{"target": "https://x.com/b"}],
                                     "headings": [{"level": 1, "text": "Path", "path": ""}]})
    ws.write_text("parsed/pa.md", "# Path Parameters\n\nHow to use path params in FastAPI.")
    ws.write_json("parsed/pb.json", {"id": "pb", "url": "https://x.com/b", "ok": True,
                                     "title": "Dependencies", "lang": "en",
                                     "links": [], "headings": []})
    ws.write_text("parsed/pb.md", "# Dependencies\n\nDepends on FastAPI Depends.")
    ws.write_text("chunks/pa.jsonl", json.dumps({
        "chunk_id": "c1", "page_id": "pa", "url": "https://x.com/a",
        "section_path": "Path", "heading": "Path", "text": "path params text",
        "token_est": 10, "version": "", "lang": "en", "has_code": False,
        "ordinal": 0, "title": "Path Parameters"}) + "\n")
    return ws


async def test_build_graph_with_fake_llm(tmp_path):
    ws = _make_workspace(tmp_path)
    graph = await build_graph(ws, FakeLLM())

    assert len(graph["concepts"]) == 2
    # 概念↔页面/chunk 双向映射
    assert graph["concepts"][0]["pages"] == ["pa", "pb"]
    assert graph["concepts"][0]["chunks"] == ["c1"]
    # 依赖边：越界引用被丢弃
    deps = [e for e in graph["concept_edges"] if e["type"] == "depends"]
    assert len(deps) == 1 and deps[0]["from"] == 0 and deps[0]["to"] == 1
    # 引用边提升：pa→pb 页面边 → 概念0→概念1
    refs = [e for e in graph["concept_edges"] if e["type"] == "reference"]
    assert any(e["from"] == 0 and e["to"] == 1 for e in refs)
    # 落盘
    assert (ws.path("graph.json")).exists()


def test_page_edges_deterministic(tmp_path):
    ws = _make_workspace(tmp_path)
    from quickstudy.knowledge.graph import _load_pages

    pages = _load_pages(ws)
    edges, _ = build_page_edges(pages)
    assert edges == [{"from": 0, "to": 1, "type": "reference"}]


def test_extract_json_tolerant():
    assert extract_json('前缀```json\n{"a": 1}\n```后缀') == {"a": 1}
    assert extract_json('[1, 2, 3]') == [1, 2, 3]


def test_fake_embedder_deterministic():
    emb = FakeEmbedder(dim=32)
    v1 = emb.embed(["hello world"])[0]
    v2 = emb.embed(["hello world"])[0]
    assert v1 == v2 and len(v1) == 32


def test_chunk_index_roundtrip(tmp_path):
    emb = FakeEmbedder(dim=32)
    idx = ChunkIndex(tmp_path / "q", emb.dim)
    chunks = [{"chunk_id": "c1", "page_id": "pa", "url": "https://x.com/a",
               "section_path": "S", "version": "", "title": "T",
               "text": "fastapi dependency injection"}]
    idx.upsert_chunks(chunks, emb.embed([chunks[0]["text"]]))
    assert idx.count() == 1
    hits = idx.search(emb.embed(["dependency injection"])[0], k=1)
    assert hits and hits[0]["chunk_id"] == "c1"
    assert hits[0]["url"] == "https://x.com/a"


def test_glossary_candidates(tmp_path):
    ws = _make_workspace(tmp_path)
    graph = {"concepts": [{"name": "依赖注入（Dependency Injection）", "description": ""}],
             "pages": [{"page_id": "pa", "title": "Path Parameters"}]}
    cands = collect_term_candidates(ws, graph)
    assert "Dependency Injection" in cands
