"""Web 任务注册表单测。"""
import json

import pytest

from quickstudy.web.jobs import JobStore


def test_create_and_get(tmp_path):
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com", with_demos=False, max_pages=50)
    assert j["status"] == "queued" and j["with_demos"] is False
    assert j["task_id"] == "fastapi-tiangolo-com"
    got = s.get(j["id"])
    assert got["url"] == j["url"] and got["cancel_requested"] is False


def test_list_order_and_update(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    b = s.create("https://b.com")
    s.update(a["id"], status="crawling")
    jobs = s.list()
    assert jobs[0]["id"] == b["id"]          # 新建在前
    assert s.get(a["id"])["status"] == "crawling"
    with pytest.raises(KeyError):
        s.update("nonexistent", status="done")


def test_find_running_and_next_queued(tmp_path):
    s = JobStore(tmp_path)
    assert s.find_running() is None and s.next_queued() is None
    a = s.create("https://a.com")
    b = s.create("https://b.com")
    assert s.next_queued()["id"] == a["id"]   # 先来先跑
    s.update(a["id"], status="organizing")
    assert s.find_running()["id"] == a["id"]
    assert s.next_queued()["id"] == b["id"]
    s.update(a["id"], status="awaiting_confirm")
    assert s.find_running() is None           # 挂起闸门不占 worker


def test_cancelled_queued_not_picked(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    s.update(a["id"], cancel_requested=True)
    assert s.next_queued() is None


def test_mark_interrupted(tmp_path):
    s = JobStore(tmp_path)
    a = s.create("https://a.com")
    s.update(a["id"], status="writing")
    n = s.mark_interrupted()
    assert n == 1
    j = s.get(a["id"])
    assert j["status"] == "failed" and j["interrupted"] is True


def _make_artifacts(ws_root):
    """构造最小产物集：manifest + graph + glossary + outline + chapters/state + llm_cost + job.log"""
    d = ws_root / "fastapi-tiangolo-com"
    (d / "chapters").mkdir(parents=True)
    (d / "demos" / "p1" / "g1").mkdir(parents=True)
    (d / "manifest.json").write_text(json.dumps({"pages": {
        "https://x/a": {"parsed": True}, "https://x/b": {"parsed": False}}}))
    (d / "graph.json").write_text(json.dumps(
        {"concepts": [{"name": "c1"}], "concept_edges": [{"from": 0, "to": 0}]}))
    (d / "glossary.json").write_text(json.dumps({"n_terms": 7, "terms": {}}))
    (d / "outline.json").write_text(json.dumps(
        {"book_title": "测试手册", "chapters": [{"no": 1}, {"no": 2}]}))
    (d / "chapters" / "state.json").write_text(json.dumps({"written": {"1": {}}}))
    (d / "demos" / "p1" / "g1" / "exec_report.json").write_text(
        json.dumps({"status": "passed"}))
    (d / "llm_cost_m4_book.json").write_text(json.dumps(
        {"total_tokens": {"in": 100, "out": 50}}))
    (d / "job.log").write_text("\n".join(f"line{i}" for i in range(30)))


def test_derive_progress(tmp_path):
    _make_artifacts(tmp_path)
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com")
    p = s.derive_progress(j)
    assert p["detail"]["crawl"] == {"discovered": 2, "parsed": 1}
    assert p["detail"]["organize"] == {"concepts": 1, "edges": 1}
    assert p["detail"]["glossary_terms"] == 7
    assert p["detail"]["demos"] == {"done": 1, "passed": 1}
    assert p["detail"]["outline"] == {"book_title": "测试手册", "chapters": 2}
    assert p["detail"]["writing"] == {"written": 1, "total": 2}
    assert p["cost"] == {"tokens_in": 100, "tokens_out": 50}
    assert len(p["recent_log"]) == 20 and p["recent_log"][-1] == "line29"


def test_derive_progress_empty(tmp_path):
    s = JobStore(tmp_path)
    j = s.create("https://fastapi.tiangolo.com")
    p = s.derive_progress(j)
    assert p["detail"] == {} and p["cost"] == {"tokens_in": 0, "tokens_out": 0}
    assert p["recent_log"] == []
