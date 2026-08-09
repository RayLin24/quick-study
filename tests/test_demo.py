"""M3 单测：候选选择、符号护栏、注释纯度校验（不依赖 Docker/LLM）。"""
import json

from quickstudy.demo.annotate import annotation_is_pure
from quickstudy.demo.generate import extract_target_symbols, symbol_guard
from quickstudy.demo.select import select_candidates
from quickstudy.storage import Workspace


def _ws_with_code(tmp_path):
    ws = Workspace(tmp_path / "task")
    ws.write_json("manifest.json", {"pages": {
        "https://x.com/a": {"parsed": True, "page_id": "pa", "sidebar_index": 2},
        "https://x.com/b": {"parsed": True, "page_id": "pb", "sidebar_index": 5},
    }})
    ws.write_json("parsed/pa.json", {
        "id": "pa", "url": "https://x.com/a", "ok": True, "title": "Path Params",
        "code_blocks": [
            {"language": "python", "code": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef root():\n    return {'msg': 'hi'}",
             "lines": 5, "section_path": "Tutorial / First Steps"},
            {"language": "console", "code": "$ uvicorn main:app", "lines": 1,
             "section_path": "Tutorial / First Steps"},
        ]})
    ws.write_json("parsed/pb.json", {
        "id": "pb", "url": "https://x.com/b", "ok": True, "title": "Short",
        "code_blocks": [{"language": "python", "code": "x = 1", "lines": 1,
                         "section_path": "S"}]})
    return ws


def test_select_groups_and_filters(tmp_path):
    ws = _ws_with_code(tmp_path)
    cands = select_candidates(ws, limit=10)
    assert len(cands) == 1                    # pb 行数不足被过滤
    g = cands[0]
    assert g["page_id"] == "pa" and g["total_lines"] == 5   # console 块不计入
    assert g["section_path"] == "Tutorial / First Steps"


def test_extract_target_symbols():
    snippets = ["from fastapi import FastAPI, Query\napp = FastAPI()\n@app.get('/items')\ndef f(q: str = Query(None)):"]
    symbols = extract_target_symbols(snippets)
    assert "FastAPI" in symbols and "Query" in symbols and "@app.get" in symbols
    assert "None" not in symbols


def test_symbol_guard():
    original = ["FastAPI", "Query", "@app.get", "APIRouter"]
    good = "FastAPI Query @app.get APIRouter"
    bad = "FastAPI Query"   # 丢了一半符号
    ok, missing = symbol_guard(original, good)
    assert ok and not missing
    ok, missing = symbol_guard(original, bad)
    assert not ok and "@app.get" in missing
    # 空符号列表不设防
    assert symbol_guard([], "anything")[0]


def test_annotation_purity():
    before = "import os\n\nx = 1  # keep\nprint(x)\n"
    # 纯注释 + docstring：通过
    after_pure = '"""模块说明文档字符串。\n\n怎么运行：python main.py\n"""\n# 导入系统模块\nimport os  # os 模块\n\n# 赋值\nx = 1  # keep\nprint(x)  # 打印\n'
    assert annotation_is_pure(before, after_pure)
    # 改值：拒绝
    assert not annotation_is_pure(before, "import os\nx = 2\nprint(x)\n")
    # 删行：拒绝
    assert not annotation_is_pure(before, "import os\nprint(x)\n")
    # 字符串里的 # 不应被误当注释剥离
    tricky_before = 'msg = "C# rocks"  # 语言\nprint(msg)\n'
    tricky_after = '# 头部注释\nmsg = "C# rocks"  # 语言\nprint(msg)\n'
    assert annotation_is_pure(tricky_before, tricky_after)
    assert not annotation_is_pure(tricky_before, 'msg = "C rocks"\nprint(msg)\n')
