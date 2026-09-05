import pytest

from nodes import FetchRepo, clip_snippets, clip_snippets_with_stats


def test_clip_snippets_includes_source_path():
    text = clip_snippets({"pkg/mod.py": "print(1)\n"}, max_total_chars=2000, max_file_chars=500)
    assert "--- File: pkg/mod.py ---" in text
    assert "# source: pkg/mod.py" in text
    assert "print(1)" in text


def test_clip_snippets_reports_truncation_stats():
    text, stats = clip_snippets_with_stats(
        {"big.py": "x" * 200, "kept.py": "ok"},
        max_total_chars=80,
        max_file_chars=20,
    )
    assert stats["file_count"] == 2
    assert stats["truncated_files"] >= 1
    assert "big.py" in text or stats["omitted_files"] >= 1


def test_fetch_repo_refuses_over_threshold(monkeypatch):
    files = {f"f{i}.py": "x" for i in range(81)}
    monkeypatch.setattr(
        "nodes.crawl_local_files",
        lambda **_kwargs: {"files": files, "stats": {}},
    )
    with pytest.raises(ValueError, match="QUICK_STUDY_ERROR:.*threshold"):
        FetchRepo().exec(
            {
                "repo_url": None,
                "local_dir": "/tmp",
                "token": None,
                "include_patterns": {"*.py"},
                "exclude_patterns": set(),
                "max_file_size": 1000,
                "use_relative_paths": True,
                "include_specified": False,
            }
        )


def test_fetch_repo_allows_over_threshold_when_include_specified(monkeypatch):
    files = {f"f{i}.py": "x" for i in range(81)}
    monkeypatch.setattr(
        "nodes.crawl_local_files",
        lambda **_kwargs: {"files": files, "stats": {}},
    )
    result = FetchRepo().exec(
        {
            "repo_url": None,
            "local_dir": "/tmp",
            "token": None,
            "include_patterns": {"*.py"},
            "exclude_patterns": set(),
            "max_file_size": 1000,
            "use_relative_paths": True,
            "include_specified": True,
        }
    )
    assert len(result) == 81


def test_fetch_repo_uses_repo_name_not_tree_tail():
    shared = {
        "repo_url": "https://github.com/owner/cool-lib/tree/release/1.0/src",
        "include_patterns": {"*.py"},
        "exclude_patterns": set(),
        "max_file_size": 1000,
    }
    prep = FetchRepo().prep(shared)
    assert shared["project_name"] == "cool-lib"
    assert prep["include_specified"] is False
