from unittest.mock import Mock

import pytest

from utils.patterns import should_include_file
from utils.crawl_github_files import (
    DownloadIncompleteError,
    GitHubCrawlError,
    candidate_tree_splits,
    crawl_github_files,
    github_http_error,
    parse_github_http_url,
    path_under_base,
    raise_if_downloads_incomplete,
    request_get,
    resolve_tree_ref,
    sort_files,
)


def test_parse_tree_url_and_slash_branch():
    owner, repo, remainder = parse_github_http_url(
        "https://github.com/owner/repo/tree/release/1.0/src"
    )
    assert (owner, repo, remainder) == ("owner", "repo", "release/1.0/src")
    ref, path = resolve_tree_ref(remainder, ["main", "release/1.0"])
    assert ref == "release/1.0"
    assert path == "src"


def test_parse_plain_repo_url():
    assert parse_github_http_url("https://github.com/owner/repo") == ("owner", "repo", None)


def test_candidate_splits_prefer_slash_branch_then_shorter_prefixes():
    splits = candidate_tree_splits("release/1.0/src", ["main"])
    assert splits[0] == ("release/1.0/src", "")
    assert ("release/1.0", "src") in splits
    assert ("release", "1.0/src") in splits
    known = candidate_tree_splits("release/1.0/src", ["main", "release/1.0"])
    assert known[0] == ("release/1.0", "src")


def test_github_404_is_human_readable():
    text = github_http_error(404, token=None, owner="o", repo="r")
    assert "QUICK_STUDY_ERROR:" in text
    assert "404" in text
    assert "GITHUB_TOKEN" in text


def test_invalid_url_raises():
    with pytest.raises(GitHubCrawlError, match="QUICK_STUDY_ERROR"):
        parse_github_http_url("https://example.com/x")


def test_tree_url_never_returns_none(monkeypatch):
    missing = Mock(status_code=404, text="Not Found", headers={}, json=lambda: {"message": "Not Found"})
    monkeypatch.setattr(
        "utils.crawl_github_files.requests.get",
        Mock(return_value=missing),
    )
    result = crawl_github_files("https://github.com/owner/missing/tree/release/1.0/src")
    assert result is not None
    assert isinstance(result.get("files"), dict)
    assert result["files"] == {}
    assert result.get("stats", {}).get("error")
    assert "QUICK_STUDY_ERROR:" in result["stats"]["error"]


def test_github_include_path_pattern_does_not_empty_run():
    """src/*.py must match GitHub paths, not only the basename app.py."""
    files = ["src/app.py", "src/util.py", "lib/other.py", "README.md"]
    kept = [path for path in files if should_include_file(path, {"src/*.py"}, None)]
    assert kept == ["src/app.py", "src/util.py"]
    assert should_include_file("pkg/mod.py", {"*.py"}, None)


def test_path_under_base_rejects_sibling_directories():
    assert path_under_base("cookbook/agent/main.py", "cookbook/agent")
    assert path_under_base("cookbook/agent", "cookbook/agent")
    assert not path_under_base("cookbook/agent-skills/main.py", "cookbook/agent")
    assert path_under_base("any/file.py", "")


def test_sort_files_is_stable_by_path():
    assert list(sort_files({"b.py": "2", "a.py": "1"})) == [("a.py", "1"), ("b.py", "2")]


def test_incomplete_downloads_fail_closed():
    with pytest.raises(DownloadIncompleteError, match="2/3"):
        raise_if_downloads_incomplete(["a.py", "b.py"], wanted=3)


def test_complete_downloads_pass():
    raise_if_downloads_incomplete([], wanted=3)


def test_request_get_retries_rate_limit_then_succeeds():
    limited = Mock(status_code=429, text="rate limit exceeded", headers={})
    ok = Mock(status_code=200, text="ok", headers={})
    getter = Mock(side_effect=[limited, ok])

    response = request_get(
        "https://api.github.com/repos/o/r/contents/",
        headers={},
        getter=getter,
        sleep=lambda _seconds: None,
        retries=3,
    )

    assert response.text == "ok"
    assert getter.call_count == 2


def test_request_get_retries_403_rate_limit():
    limited = Mock(
        status_code=403,
        text="API rate limit exceeded",
        headers={"X-RateLimit-Reset": "0"},
    )
    ok = Mock(status_code=200, text="ok", headers={})
    getter = Mock(side_effect=[limited, ok])

    response = request_get(
        "https://api.github.com/repos/o/r/contents/",
        getter=getter,
        sleep=lambda _seconds: None,
        retries=3,
    )

    assert response.status_code == 200
    assert getter.call_count == 2
