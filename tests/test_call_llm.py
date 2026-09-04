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
    llm.reset_empty_stream_streak()
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
    llm.reset_empty_stream_streak()
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setattr(llm, "stream_fallback_enabled", True)
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


def test_default_provider_is_openrouter_glm(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gemini-should-not-win")
    assert llm.get_llm_provider() == "OPENROUTER"
    assert llm.DEFAULT_OPENROUTER_MODEL == "z-ai/glm-5.3-flash"
    assert llm.DEFAULT_OPENROUTER_BASE_URL == "https://openrouter.ai/api"
    assert llm.DEFAULT_TIMEOUT_SECONDS == 300
    assert llm.chat_completions_url(llm.DEFAULT_OPENROUTER_BASE_URL) == (
        "https://openrouter.ai/api/v1/chat/completions"
    )


def test_missing_openrouter_key_is_readable(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is not set"):
        llm.call_llm("hello", use_cache=False)


def test_placeholder_openrouter_key_is_treated_as_missing(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "<OPENROUTER_API_KEY>")
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY is not set"):
        llm.call_llm("hello", use_cache=False)


def test_openrouter_default_posts_expected_url(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("OPENROUTER_BASE_URL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    captured = {}
    lines = [
        'data: {"choices":[{"delta":{"content":"ok"}}]}',
        "data: [DONE]",
    ]

    def post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs.get("headers")
        captured["payload"] = kwargs.get("json")
        return _sse_response(lines)

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.call_llm("hello", use_cache=False) == "ok"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["payload"]["model"] == "z-ai/glm-5.3-flash"
    assert set(captured["payload"]) <= {
        "model",
        "messages",
        "temperature",
        "stream",
        "stream_options",
        "max_tokens",
    }
    assert captured["payload"]["stream"] is True


def test_reasoning_field_is_progress_only(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    lines = [
        'data: {"choices":[{"delta":{"reasoning":"planning the answer"}}]}',
        'data: {"choices":[{"delta":{"content":"visible"}}]}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_sse_response(lines)))
    assert llm.call_llm("hello", use_cache=False) == "visible"


def test_cache_key_includes_provider_and_model(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", False)
    monkeypatch.setenv("LLM_PROVIDER", "OPENROUTER")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("OPENROUTER_MODEL", "model-a")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_completion("from-a")))
    assert llm.call_llm("hello", use_cache=True) == "from-a"
    monkeypatch.setenv("OPENROUTER_MODEL", "model-b")
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=_completion("from-b")))
    assert llm.call_llm("hello", use_cache=True) == "from-b"


def test_consecutive_empty_streams_stop_fallback(tmp_path, monkeypatch):
    llm.reset_empty_stream_streak()
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setattr(llm, "stream_fallback_enabled", True)
    monkeypatch.setattr(llm, "empty_stream_max", 2)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    empty = _sse_response(
        [
            'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}',
            "data: [DONE]",
        ]
    )
    blocked = _completion("from blocking")
    calls = {"stream": 0, "blocking": 0}

    def post(_url, **kwargs):
        payload = kwargs.get("json") or {}
        if payload.get("stream"):
            calls["stream"] += 1
            return empty
        calls["blocking"] += 1
        return blocked

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.call_llm("first", use_cache=False) == "from blocking"
    assert calls["blocking"] == 1
    with pytest.raises(llm.EmptyLLMResponse, match="consecutive empty"):
        llm.call_llm("second", use_cache=False)
    assert calls["blocking"] == 1
    assert calls["stream"] == 2
    llm.reset_empty_stream_streak()


def test_empty_stream_fallback_is_logged(tmp_path, monkeypatch, capsys):
    llm.reset_empty_stream_streak()
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setattr(llm, "stream_fallback_enabled", True)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    empty = _sse_response(
        [
            'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}',
            "data: [DONE]",
        ]
    )
    blocked = _completion("from blocking")

    def post(_url, **kwargs):
        payload = kwargs.get("json") or {}
        return empty if payload.get("stream") else blocked

    monkeypatch.setattr(llm.requests, "post", post)
    assert llm.call_llm("hello", use_cache=False) == "from blocking"
    captured = capsys.readouterr().out
    assert "QUICK_STUDY_ERROR:" in captured
    assert "empty stream" in captured.lower() or "non-stream" in captured.lower()


def test_empty_stream_does_not_fallback_by_default(tmp_path, monkeypatch):
    llm.reset_empty_stream_streak()
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", True)
    monkeypatch.setattr(llm, "stream_fallback_enabled", False)
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    empty = _sse_response(
        [
            'data: {"choices":[{"delta":{"reasoning":"thinking"}}]}',
            "data: [DONE]",
        ]
    )
    blocked = _completion("from blocking")
    calls = {"n": 0}

    def post(_url, **kwargs):
        calls["n"] += 1
        payload = kwargs.get("json") or {}
        return empty if payload.get("stream") else blocked

    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(llm.EmptyLLMResponse):
        llm.call_llm("hello", use_cache=False)
    assert calls["n"] == 1


def test_blocking_usage_is_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(llm, "cache_dir", str(tmp_path / "cache"))
    monkeypatch.setattr(llm, "legacy_cache_file", str(tmp_path / "missing.json"))
    monkeypatch.setattr(llm, "stream_enabled", False)
    monkeypatch.setattr(llm, "usage_meter", llm.UsageMeter())
    monkeypatch.setenv("LLM_PROVIDER", "DEEPSEEK")
    monkeypatch.setenv("DEEPSEEK_MODEL", "m")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.example.test")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    response = _completion("ok")
    response.json.return_value["usage"] = {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    monkeypatch.setattr(llm.requests, "post", lambda *_a, **_k: response)
    assert llm.call_llm("hello", use_cache=False) == "ok"
    snap = llm.usage_meter.snapshot()
    assert snap["prompt_tokens"] == 11
    assert snap["completion_tokens"] == 7
    assert snap["total_tokens"] == 18


def test_missing_openrouter_key_has_stable_prefix(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    with pytest.raises(ValueError, match="QUICK_STUDY_ERROR:.*OPENROUTER_API_KEY"):
        llm.call_llm("hello", use_cache=False)
