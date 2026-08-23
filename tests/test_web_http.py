from pathlib import Path

from fastapi.testclient import TestClient

from webapp import create_app


def _app(tmp_path: Path, runner):
    output = tmp_path / "output"
    output.mkdir(exist_ok=True)
    return create_app(output_dir=output, runner=runner, python_exe="python")


def test_start_job_and_read_tutorial(tmp_path: Path):
    output = tmp_path / "output"

    def runner(cmd, on_line, cwd):
        dest = output / "repo"
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "index.md").write_text("# Hello\n\n[Ch](01_one.md)\n", encoding="utf-8")
        (dest / "01_one.md").write_text("# One", encoding="utf-8")
        on_line(f"Tutorial generation complete! Files are in: {dest}")
        return 0

    client = TestClient(_app(tmp_path, runner))
    started = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo", "language": "Chinese"},
    )
    assert started.status_code == 200
    client.app.state.manager.wait(timeout=5)

    page = client.get("/t/repo")
    assert page.status_code == 200
    assert "Hello" in page.text
    assert 'href="/t/repo/01_one.md"' in page.text


def test_path_escape_is_rejected(tmp_path: Path):
    (tmp_path / "secret.md").write_text("nope", encoding="utf-8")
    tutorial = tmp_path / "output" / "Demo"
    tutorial.mkdir(parents=True)
    (tutorial / "index.md").write_text("# ok", encoding="utf-8")
    client = TestClient(_app(tmp_path, lambda cmd, on_line, cwd: 0))
    res = client.get("/t/Demo/../secret.md")
    assert res.status_code in (400, 404)


def test_second_job_returns_conflict(tmp_path: Path):
    gate = tmp_path / "gate"

    def runner(cmd, on_line, cwd):
        while not gate.exists():
            pass
        return 0

    client = TestClient(_app(tmp_path, runner))
    first = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/repo"},
    )
    assert first.status_code == 200
    second = client.post(
        "/api/jobs",
        json={"source_type": "repo", "repo_url": "https://github.com/owner/other"},
    )
    assert second.status_code == 409
    gate.write_text("ok", encoding="utf-8")
    client.app.state.manager.wait(timeout=5)
