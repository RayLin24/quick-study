"""OpenAI-compatible gateways: any model name, credentials from the environment."""

from __future__ import annotations

import httpx
import pytest

from app.llm.errors import ModelCredentialsMissing, ModelInvalidRequest
from app.llm.factory import build_chat_model
from app.llm.providers.base import CompletionRequest, ModelCapability, user_message
from app.llm.providers.openai_compatible import (
    LLM_API_KEY_ENV,
    OpenAICompatibleSettings,
    build_openai_compatible_chat_model,
    chat_completions_url,
)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(LLM_API_KEY_ENV, raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)


def test_chat_url_appends_the_openai_path() -> None:
    assert (
        chat_completions_url("http://111.230.91.51:18123/")
        == "http://111.230.91.51:18123/v1/chat/completions"
    )
    assert (
        chat_completions_url("http://example.test/v1")
        == "http://example.test/v1/chat/completions"
    )


def test_missing_credentials_are_refused(clean_env: None) -> None:
    with pytest.raises(ModelCredentialsMissing):
        build_openai_compatible_chat_model(settings=OpenAICompatibleSettings(_env_file=None))


def test_an_unregistered_model_is_still_accepted(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "sk-test")
    monkeypatch.setenv("LLM_MODEL", "glm5.3")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test")

    model = build_openai_compatible_chat_model()

    assert model.spec.name == "glm5.3"
    assert model.spec.supports(ModelCapability.STRUCTURED_OUTPUT)


def test_factory_picks_the_compatible_gateway(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "sk-test")
    monkeypatch.setenv("LLM_MODEL", "glm5.3")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test")

    model = build_chat_model()

    assert model.spec.provider == "openai-compatible"
    assert model.spec.name == "glm5.3"


def test_complete_reads_an_openai_style_reply(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "sk-test")
    monkeypatch.setenv("LLM_MODEL", "glm5.3")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/v1/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-test"
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"title":"ok"}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 4},
            },
        )

    model = build_openai_compatible_chat_model(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    completion = model.complete(
        CompletionRequest(messages=(user_message("hi"),), response_schema={"type": "object"})
    )

    assert completion.text == '{"title":"ok"}'
    assert completion.usage.input_tokens == 11
    assert completion.usage.output_tokens == 4


def test_complete_reads_list_content_and_reasoning(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "sk-test")
    monkeypatch.setenv("LLM_MODEL", "glm5.3")
    monkeypatch.setenv("LLM_BASE_URL", "http://example.test")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "text", "text": '{"ok":true}'}],
                            "reasoning_content": "thinking",
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    model = build_openai_compatible_chat_model(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    completion = model.complete(CompletionRequest(messages=(user_message("hi"),)))

    assert completion.text == '{"ok":true}'


def test_http_errors_keep_their_status(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(LLM_API_KEY_ENV, "sk-test")
    monkeypatch.setenv("LLM_MODEL", "glm5.3")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "bad model"}})

    model = build_openai_compatible_chat_model(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )
    with pytest.raises(ModelInvalidRequest) as failure:
        model.complete(CompletionRequest(messages=(user_message("hi"),)))
    assert failure.value.status_code == 400
    assert "sk-test" not in str(failure.value)
