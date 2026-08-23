from unittest.mock import Mock

import requests

from utils.crawl_github_files import download_file_text, request_get


def test_request_get_retries_ssl_error_then_succeeds():
    ok = Mock(status_code=200, text="ok", headers={})
    getter = Mock(side_effect=[requests.exceptions.SSLError("UNEXPECTED_EOF_WHILE_READING"), ok])

    response = request_get(
        "https://raw.githubusercontent.com/owner/repo/main/a.py",
        headers={},
        getter=getter,
        sleep=lambda _seconds: None,
        retries=3,
    )

    assert response.text == "ok"
    assert getter.call_count == 2


def test_download_file_text_falls_back_to_api_after_raw_ssl_failure():
    api_body = Mock(
        status_code=200,
        json=lambda: {
            "encoding": "base64",
            "content": "cHJpbnQoImhpIik=\n",
        },
    )
    getter = Mock(side_effect=[requests.exceptions.SSLError("eof"), api_body])
    item = {
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/a.py",
        "url": "https://api.github.com/repos/owner/repo/contents/a.py",
        "path": "a.py",
    }

    text = download_file_text(
        item,
        api_headers={"Accept": "application/vnd.github.v3+json", "Authorization": "token abc"},
        getter=getter,
        sleep=lambda _seconds: None,
        retries=1,
    )

    assert text == 'print("hi")'
    raw_call_headers = getter.call_args_list[0].kwargs["headers"]
    api_call_headers = getter.call_args_list[1].kwargs["headers"]
    assert "Authorization" in raw_call_headers
    assert "Accept" not in raw_call_headers
    assert api_call_headers["Accept"].startswith("application/vnd.github")


def test_download_file_text_returns_none_instead_of_raising_after_retries():
    getter = Mock(side_effect=requests.exceptions.SSLError("eof"))
    item = {
        "download_url": "https://raw.githubusercontent.com/owner/repo/main/a.py",
        "url": "https://api.github.com/repos/owner/repo/contents/a.py",
        "path": "a.py",
    }

    text = download_file_text(
        item,
        api_headers={},
        getter=getter,
        sleep=lambda _seconds: None,
        retries=2,
    )

    assert text is None
    assert getter.call_count == 4
