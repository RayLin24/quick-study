"""Checklist #9–10: this-repo pytest CI + README/footer install target."""

from pathlib import Path

from nodes import GENERATOR_ATTRIBUTION, CombineTutorial

ROOT = Path(__file__).resolve().parents[1]
THIS_REPO = "https://github.com/RayLin24/quick-study"
UPSTREAM_REPO = "https://github.com/The-Pocket/PocketFlow-Tutorial-Codebase-Knowledge"
OLD_BUILDER = "https://github.com/The-Pocket/Tutorial-Codebase-Knowledge"
CLONE_THIS = f"git clone {THIS_REPO}"
CLONE_UPSTREAM = f"git clone {UPSTREAM_REPO}"


def _workflow_text(name: str) -> str:
    return (ROOT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_9_pytest_ci_runs_on_push_and_pull_request():
    # Raw text: PyYAML 1.1 treats the `on:` key as boolean True.
    text = _workflow_text("pytest.yml")
    assert text.startswith("# This repo's own CI")
    assert "name: pytest" in text
    assert "\n  push:" in text or "\non:\n  push:" in text
    assert "pull_request:" in text
    assert "workflow_dispatch" not in text
    assert "python -m pytest" in text
    assert "pip install -r requirements.txt" in text
    assert "OPENROUTER_API_KEY" not in text
    assert "secrets." not in text


def test_9_consumer_workflow_dispatch_template_untouched():
    text = _workflow_text("quick-study.yml")
    assert "name: quick-study" in text
    assert "workflow_dispatch:" in text
    assert "python -m pytest" not in text
    assert "main.py" in text


def test_10_readme_clone_this_repo_and_one_upstream_line():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert CLONE_THIS in text
    assert CLONE_UPSTREAM not in text
    based = [line for line in text.splitlines() if "本仓库基于" in line and "PocketFlow-Tutorial-Codebase-Knowledge" in line]
    assert len(based) == 1, based
    assert UPSTREAM_REPO in based[0]


def test_10_chapter_footer_points_to_this_repo():
    assert THIS_REPO in GENERATOR_ATTRIBUTION
    assert "Tutorial-Codebase-Knowledge" not in GENERATOR_ATTRIBUTION
    assert OLD_BUILDER not in GENERATOR_ATTRIBUTION

    shared = {
        "project_name": "Demo",
        "output_dir": "/tmp/out",
        "repo_url": "https://github.com/o/r",
        "relationships": {"summary": "sum", "details": [{"from": 0, "to": 1, "label": "uses"}]},
        "chapter_order": [0, 1],
        "abstractions": [
            {"name": "入口", "description": "d", "files": []},
            {"name": "核心", "description": "d", "files": []},
        ],
        "chapters": [
            "# Chapter 1: 入口\n\n" + ("说明。" * 40),
            "# Chapter 2: 核心\n\n" + ("说明。" * 40),
        ],
        "language": "Chinese",
        "file_count": 2,
        "map_mode": False,
    }
    prep = CombineTutorial().prep(shared)
    assert GENERATOR_ATTRIBUTION in prep["index_content"]
    assert OLD_BUILDER not in prep["index_content"]
    assert all(GENERATOR_ATTRIBUTION in item["content"] for item in prep["chapter_files"])
    assert all(OLD_BUILDER not in item["content"] for item in prep["chapter_files"])
