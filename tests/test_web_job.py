import os
import sys
import time
from pathlib import Path

import pytest

from web.job import JobBusyError, JobManager, _exit_reason, build_command, validate_start_request


def test_validate_repo_requires_github_url():
    with pytest.raises(ValueError, match="GitHub"):
        validate_start_request({"source_type": "repo", "repo_url": "https://example.com/x"})


def test_validate_dir_requires_existing_directory(tmp_path: Path):
    missing = tmp_path / "nope"
    with pytest.raises(ValueError, match="目录"):
        validate_start_request({"source_type": "dir", "local_dir": str(missing)})


def test_validate_dir_accepts_existing_directory(tmp_path: Path):
    payload = validate_start_request({"source_type": "dir", "local_dir": str(tmp_path)})
    assert Path(payload["local_dir"]) == tmp_path.resolve()


def test_tree_url_is_accepted():
    payload = validate_start_request(
        {
            "source_type": "repo",
            "repo_url": "https://github.com/owner/repo/tree/release/1.0/src",
        }
    )
    assert payload["repo_url"].endswith("/tree/release/1.0/src")


def test_include_exclude_max_size_go_on_command(tmp_path: Path):
    cmd = build_command(
        {
            "source_type": "repo",
            "repo_url": "https://github.com/owner/repo",
            "include": "*.py *.md",
            "exclude": "tests/*",
            "max_size": 50000,
        },
        python_exe="python",
        main_py=tmp_path / "main.py",
        output_dir=tmp_path / "output",
    )
    assert "--include" in cmd and "*.py" in cmd and "*.md" in cmd
    assert "--exclude" in cmd and "tests/*" in cmd
    assert "--max-size" in cmd and "50000" in cmd
    assert "--token" not in cmd


def test_default_language_is_chinese():
    payload = validate_start_request(
        {"source_type": "repo", "repo_url": "https://github.com/owner/repo"}
    )
    assert payload["language"] == "Chinese"


def test_exit_reason_includes_missing_key():
    reason = _exit_reason(
        [
            "Starting tutorial generation",
            "ValueError: OPENROUTER_API_KEY is not set. Copy .env.sample to .env",
        ],
        1,
    )
    assert "进程退出码 1" in reason
    assert "OPENROUTER_API_KEY" in reason


def test_build_command_for_repo(tmp_path: Path):
    cmd = build_command(
        {
            "source_type": "repo",
            "repo_url": "https://github.com/owner/repo",
            "language": "Chinese",
            "name": "Demo",
            "github_token": "tok",
            "max_abstractions": 8,
        },
        python_exe="python",
        main_py=tmp_path / "main.py",
        output_dir=tmp_path / "output",
    )
    assert cmd[:4] == ["python", str(tmp_path / "main.py"), "--repo", "https://github.com/owner/repo"]
    assert "--language" in cmd and "Chinese" in cmd
    assert "--name" in cmd and "Demo" in cmd
    assert "--token" not in cmd
    assert "--max-abstractions" in cmd and "8" in cmd
    assert "--output" in cmd and str(tmp_path / "output") in cmd


def test_job_manager_rejects_second_start_while_running(tmp_path: Path):
    release = tmp_path / "go"
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def blocking_runner(cmd, on_line, cwd):
        while not release.exists():
            pass
        on_line(f"Tutorial generation complete! Files are in: {output_dir / 'demo'}")
        return 0

    manager = JobManager(
        output_dir=output_dir,
        python_exe="python",
        main_py=tmp_path / "main.py",
        cwd=tmp_path,
        runner=blocking_runner,
    )
    first = manager.start(
        {"source_type": "repo", "repo_url": "https://github.com/owner/repo", "language": "Chinese"}
    )
    assert first["status"] == "running"
    with pytest.raises(JobBusyError):
        manager.start(
            {"source_type": "repo", "repo_url": "https://github.com/owner/other", "language": "Chinese"}
        )
    release.write_text("ok", encoding="utf-8")
    manager.wait(timeout=5)
    assert manager.snapshot()["status"] == "succeeded"


def test_job_manager_failed_snapshot_includes_reason(tmp_path: Path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()

    def failing_runner(cmd, on_line, cwd):
        on_line("ValueError: OPENROUTER_API_KEY is not set. Copy .env.sample to .env")
        return 1

    manager = JobManager(
        output_dir=output_dir,
        python_exe="python",
        main_py=tmp_path / "main.py",
        cwd=tmp_path,
        runner=failing_runner,
    )
    manager.start({"source_type": "repo", "repo_url": "https://github.com/owner/repo"})
    manager.wait(timeout=5)
    snap = manager.snapshot()
    assert snap["status"] == "failed"
    assert "OPENROUTER_API_KEY" in snap["error"]
    assert "进程退出码 1" in snap["error"]
    assert "QUICK_STUDY_ERROR:" in snap["error"]


def test_cancel_kills_slow_child(tmp_path: Path):
    main_py = tmp_path / "main.py"
    main_py.write_text("import time\nprint('child-start', flush=True)\ntime.sleep(60)\n", encoding="utf-8")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    manager = JobManager(
        output_dir=output_dir,
        python_exe=sys.executable,
        main_py=main_py,
        cwd=tmp_path,
    )
    manager.start({"source_type": "repo", "repo_url": "https://github.com/owner/repo"})
    for _ in range(50):
        logs = manager.snapshot()["logs"]
        if any("child-start" in line for line in logs):
            break
        time.sleep(0.05)
    child_pid = manager._job.proc.pid if manager._job and manager._job.proc else None
    snap = manager.cancel()
    assert snap["status"] in ("running", "cancelled")
    manager.wait(timeout=5)
    final = manager.snapshot()
    assert final["status"] == "cancelled"
    assert final["error"]
    assert "QUICK_STUDY_ERROR:" in final["error"]
    persisted = (output_dir / "current.json").read_text(encoding="utf-8")
    assert "github_token" not in persisted
    if child_pid:
        with pytest.raises(OSError):
            os.kill(child_pid, 0)


def test_log_cursor_does_not_replay_dropped_prefix(tmp_path: Path, monkeypatch):
    from web import job as jobmod

    monkeypatch.setattr(jobmod, "LOG_RING", 3)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    j = jobmod.Job({"source_type": "repo"}, timeout=10)
    for i in range(5):
        j.append_log(f"line-{i}")
    snap = j.snapshot(after=0)
    assert snap["log_start"] == 2
    assert snap["logs"] == ["line-2", "line-3", "line-4"]
    later = j.snapshot(after=4)
    assert later["logs"] == ["line-4"]


def test_success_without_complete_line_uses_newest_tutorial(tmp_path: Path):
    output_dir = tmp_path / "output"
    dest = output_dir / "FallbackDemo"
    dest.mkdir(parents=True)
    (dest / "index.md").write_text("# hi\n", encoding="utf-8")

    def silent_success(cmd, on_line, cwd):
        return 0

    manager = JobManager(
        output_dir=output_dir,
        python_exe="python",
        main_py=tmp_path / "main.py",
        cwd=tmp_path,
        runner=silent_success,
    )
    manager.start({"source_type": "repo", "repo_url": "https://github.com/owner/repo"})
    manager.wait(timeout=5)
    snap = manager.snapshot()
    assert snap["status"] == "succeeded"
    assert snap["output_name"] == "FallbackDemo"
