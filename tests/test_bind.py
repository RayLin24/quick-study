import os

import pytest

from web.bind import BindRefused, assert_safe_bind, is_loopback_host
from web.serve import main as serve_main


def test_loopback_hosts_do_not_need_token():
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("localhost")
    assert is_loopback_host("::1")
    assert_safe_bind("127.0.0.1", token="")
    assert_safe_bind("localhost", token=None)


def test_non_loopback_without_token_refuses():
    with pytest.raises(BindRefused, match="QUICK_STUDY_TOKEN"):
        assert_safe_bind("0.0.0.0", token="")
    with pytest.raises(BindRefused, match="QUICK_STUDY"):
        assert_safe_bind("192.168.1.10", token=None)


def test_non_loopback_with_token_starts():
    assert_safe_bind("0.0.0.0", token="secret")


def test_serve_cli_refuses_public_bind(monkeypatch, capsys):
    monkeypatch.delenv("QUICK_STUDY_TOKEN", raising=False)

    def boom(*_args, **_kwargs):
        raise AssertionError("uvicorn.run should not be called")

    monkeypatch.setattr("web.serve.uvicorn.run", boom)
    code = serve_main(["--host", "0.0.0.0", "--port", "8000"])
    assert code == 2
    err = capsys.readouterr().err
    assert "QUICK_STUDY_TOKEN" in err
    assert "0.0.0.0" in err
    assert "拒绝启动" in err
    assert "非回环" in err or "公网" in err
    assert "启动已中止" in err
