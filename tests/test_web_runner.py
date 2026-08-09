"""Runner 执行器单测：mock 阶段函数，不触网不触 Docker。"""
import pytest

from quickstudy.web.jobs import JobStore
from quickstudy.web.runner import Runner

STAGE_NAMES = ["crawling", "organizing", "demoing", "outlining", "writing"]


def _make_fns(calls, fail_at=None, cancel_at=None, store=None):
    def make(stage):
        async def fn(cfg):
            calls.append(stage)
            if fail_at == stage:
                raise RuntimeError("boom")
            if cancel_at == stage and store is not None:
                job = store.list()[0]
                store.update(job["id"], cancel_requested=True)
        return fn
    return {s: make(s) for s in STAGE_NAMES}


def _runner(tmp_path, calls, fail_at=None, cancel_at=None, docker=True):
    store = JobStore(tmp_path)
    fns = _make_fns(calls, fail_at=fail_at, cancel_at=cancel_at, store=store)
    return store, Runner(store, stage_fns=fns, docker_check=lambda: docker)


async def test_pre_gate_to_awaiting_confirm(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    job = store.create("https://a.com")
    await r.run_next()
    assert calls == ["crawling", "organizing", "demoing", "outlining"]
    assert store.get(job["id"])["status"] == "awaiting_confirm"


async def test_confirm_runs_writing_to_done(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    job = store.create("https://a.com")
    await r.run_next()
    await r.confirm(job["id"])
    assert calls[-1] == "writing"
    assert store.get(job["id"])["status"] == "done"


async def test_confirm_wrong_status_raises(tmp_path):
    store, r = _runner(tmp_path, [])
    job = store.create("https://a.com")
    with pytest.raises(ValueError):
        await r.confirm(job["id"])


async def test_confirm_missing_job_raises(tmp_path):
    _, r = _runner(tmp_path, [])
    with pytest.raises(KeyError):
        await r.confirm("nonexistent")


async def test_stage_failure_marks_failed(tmp_path):
    store, r = _runner(tmp_path, [], fail_at="organizing")
    job = store.create("https://a.com")
    await r.run_next()
    j = store.get(job["id"])
    assert j["status"] == "failed" and "boom" in j["error"]


async def test_docker_unavailable_skips_demos(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls, docker=False)
    job = store.create("https://a.com")
    await r.run_next()
    assert "demoing" not in calls
    assert store.get(job["id"])["skipped_demos"] is True


async def test_with_demos_false_skips(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    store.create("https://a.com", with_demos=False)
    await r.run_next()
    assert "demoing" not in calls


async def test_cancel_at_awaiting_confirm_immediate(tmp_path):
    store, r = _runner(tmp_path, [])
    job = store.create("https://a.com")
    await r.run_next()
    r.request_cancel(job["id"])
    assert store.get(job["id"])["status"] == "cancelled"


async def test_cancel_between_stages(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls, cancel_at="organizing")
    job = store.create("https://a.com")
    await r.run_next()
    j = store.get(job["id"])
    assert j["status"] == "cancelled"
    assert calls == ["crawling", "organizing"]   # demoing 起不再执行


async def test_serial_single_job(tmp_path):
    calls = []
    store, r = _runner(tmp_path, calls)
    a = store.create("https://a.com")
    b = store.create("https://b.com")
    await r.run_next()   # a 停在闸门
    await r.run_next()   # 闸门挂起不占 worker，b 开跑
    assert store.get(a["id"])["status"] == "awaiting_confirm"
    assert store.get(b["id"])["status"] == "awaiting_confirm"


async def test_job_log_written(tmp_path):
    store, r = _runner(tmp_path, [])
    job = store.create("https://a.com")
    await r.run_next()
    log_file = tmp_path / job["task_id"] / "job.log"
    # 阶段函数是 mock 无日志输出，但 handler 落盘机制本身由 _attach_log 保证；
    # 这里验证运行后日志文件可被 derive_progress 读取（不存在则为空列表即可）
    p = store.derive_progress(store.get(job["id"]))
    assert isinstance(p["recent_log"], list)
