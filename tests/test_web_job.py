from pathlib import Path

import pytest

from web.job import JobBusyError, JobManager, build_command, validate_start_request


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
    assert "--token" in cmd and "tok" in cmd
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
