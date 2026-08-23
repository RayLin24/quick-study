from unittest.mock import Mock

import pytest

from utils.crawl_github_files import (
    DownloadIncompleteError,
    path_under_base,
    raise_if_downloads_incomplete,
    request_get,
    sort_files,
)


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
