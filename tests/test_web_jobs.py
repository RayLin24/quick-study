"""Web 任务注册表单测。"""
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
