import json
from unittest.mock import Mock

import pytest

from utils import call_llm as llm


def _sse_response(lines, status=200):
    response = Mock()
    response.status_code = status
    response.text = "no stream"
    response.iter_lines = Mock(return_value=lines)
    response.__enter__ = Mock(return_value=response)
    response.__exit__ = Mock(return_value=False)
    return response


def _completion(content: str):
    response = Mock()
    response.status_code = 200
    response.json.return_value = {
        "choices": [{"message": {"content": content}}],
    }
    response.raise_for_status = Mock()
    return response


def test_empty_sse_raises_and_does_not_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}',
        "data: [DONE]",
    ]

    def post(_url, **kwargs):
        payload = kwargs.get("json") or {}
        if payload.get("stream"):
            return _sse_response(lines)
        return _completion("   ")

    monkeypatch.setattr(llm.requests, "post", post)

    with pytest.raises(llm.EmptyLLMResponse):
        llm.call_llm("hello", use_cache=True)

    cache_dir = tmp_path / "cache"
    assert not cache_dir.exists() or list(cache_dir.glob("*.json")) == []


def test_sse_content_is_assembled(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    lines = [
        'data: {"choices":[{"delta":{"content":"Hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_sse_response(lines)))

    assert llm.call_llm("hello", use_cache=False) == "Hello world"


def test_stream_400_falls_back_to_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")

    unsupported = _sse_response([], status=400)
    blocked = _completion("from blocking")

    def post(_url, **kwargs):
        payload = kwargs.get("json") or {}
        return unsupported if payload.get("stream") else blocked

    monkeypatch.setattr(llm.requests, "post", post)

    assert llm.call_llm("hello", use_cache=False) == "from blocking"


def test_empty_stream_falls_back_to_blocking(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    empty = _sse_response(
        [
            'data: {"choices":[{"delta":{"reasoning_content":"thinking"}}]}',
            "data: [DONE]",
        ]
    )
    blocked = _completion("from blocking")

    def post(_url, **kwargs):
        payload = kwargs.get("json") or {}
        return empty if payload.get("stream") else blocked

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.call_llm("hello", use_cache=False) == "from blocking"


def test_sse_error_payload_is_not_silent_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    lines = [
        'data: {"error":{"message":"maximum context length"}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_sse_response(lines)))

    with pytest.raises(Exception, match="maximum context length"):
        llm.call_llm("hello", use_cache=False)


def test_stream_decodes_utf8_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    payload = json.dumps(
        {"choices": [{"delta": {"content": "可运行组件"}}]},
        ensure_ascii=False,
    )
    lines = [f"data: {payload}".encode("utf-8"), b"data: [DONE]"]
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_sse_response(lines)))
    assert llm.call_llm("hello", use_cache=False) == "可运行组件"


def test_stream_recovers_latin1_mojibake(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    utf8_json = json.dumps(
        {"choices": [{"delta": {"content": "可运行"}}]},
        ensure_ascii=False,
    ).encode("utf-8")
    mojibake = utf8_json.decode("latin-1")
    lines = [f"data: {mojibake}", "data: [DONE]"]
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_sse_response(lines)))
    assert llm.call_llm("hello", use_cache=False) == "可运行"


def test_prompt_log_text_truncates_huge_prompts():
    text = llm._prompt_log_text("x" * 9000, limit=100)
    assert text.startswith("PROMPT (9000 chars):")
    assert len(text) < 200
    assert llm._prompt_log_text("short") == "PROMPT: short"


def test_empty_blocking_response_is_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", False)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_completion("   ")))

    with pytest.raises(llm.EmptyLLMResponse):
        llm.call_llm("hello", use_cache=True)

    cache_dir = tmp_path / "cache"
    assert not cache_dir.exists() or list(cache_dir.glob("*.json")) == []


def test_legacy_cache_is_read_once(tmp_path, monkeypatch):
    legacy = tmp_path / "llm_cache.json"
    legacy.write_text(json.dumps({"once": "legacy"}), encoding="utf-8")
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(legacy))
    llm._legacy_cache = None
    llm._legacy_loaded = False

    assert llm.load_cache("once") == "legacy"
    legacy.write_text(json.dumps({"once": "changed"}), encoding="utf-8")
    assert llm.load_cache("once") == "legacy"
