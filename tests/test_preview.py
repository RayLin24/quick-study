from pathlib import Path

import pytest

from utils.preview import (
    PreviewError,
    estimate_llm_calls,
    format_preview_report,
    preview_generation,
)
from web.job import validate_start_request


def test_estimate_llm_calls_is_three_plus_chapters():
    est = estimate_llm_calls(10)
    assert est["identify"] == 1
    assert est["relationships"] == 1
    assert est["order"] == 1
    assert est["write_chapters"] == 10
    assert est["total"] == 13


def test_preview_local_dir_does_not_write(tmp_path: Path):
    src = tmp_path / "repo"
    src.mkdir()
    (src / "a.py").write_text("print(1)\n", encoding="utf-8")
    (src / "b.py").write_text("print(2)\n", encoding="utf-8")
    output = tmp_path / "output"
    output.mkdir()
    payload = validate_start_request(
        {
            "source_type": "dir",
            "local_dir": str(src),
            "max_abstractions": 5,
            "include": "*.py",
        }
    )
    preview = preview_generation(payload)
    assert preview["dry_run"] is True
    assert preview["file_count"] == 2
    assert preview["estimated_calls"]["total"] == 8
    assert "a.py" in preview["files_sample"]
    assert list(output.iterdir()) == []
    report = format_preview_report(preview)
    assert "只爬不写" in report
    assert "估 LLM 调用" in report


def test_preview_over_threshold_warns_without_raising(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CRAWL_FILE_THRESHOLD", "3")
    src = tmp_path / "repo"
    src.mkdir()
    for i in range(5):
        (src / f"f{i}.py").write_text("x\n", encoding="utf-8")
    payload = {
        "source_type": "dir",
        "local_dir": str(src),
        "include_patterns": {"*.py"},
        "exclude_patterns": set(),
        "max_file_size": 1000,
        "include_specified": False,
        "max_abstractions": 4,
    }
    preview = preview_generation(payload)
    assert preview["ok"] is False
    assert preview["over_threshold"] is True
    assert preview["file_count"] == 5
    assert preview["warning"]


def test_preview_empty_dir_fails(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PreviewError, match="Failed to fetch files"):
        preview_generation(
            {
                "local_dir": str(empty),
                "include_patterns": {"*.py"},
                "exclude_patterns": set(),
                "max_file_size": 1000,
            }
        )


def test_cli_dry_run_exits_before_flow(tmp_path: Path, monkeypatch):
    src = tmp_path / "code"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")

    import main as mainmod

    monkeypatch.setattr(
        "sys.argv",
        ["main.py", "--dir", str(src), "--include", "*.py", "--dry-run", "--max-abstractions", "6"],
    )
    ran = {"flow": False}

    def boom(_shared):
        ran["flow"] = True
        raise AssertionError("dry-run must not start the flow")

    class FakeFlow:
        def run_async(self, shared):
            boom(shared)

    monkeypatch.setattr(mainmod, "create_tutorial_flow", lambda: FakeFlow())
    with pytest.raises(SystemExit) as exc:
        mainmod.main()
    assert exc.value.code == 0
    assert ran["flow"] is False
