"""The default provider: DeepSeek through the official ``langchain-deepseek`` package.

Two things are load-bearing here. The API key is read from the server's configuration and
nowhere else, so it can never end up in the repository, a prompt or an error message. And
the model registry records which model can actually be constrained to JSON: the reasoning
model cannot, so asking it for structured output has to fail before a request is sent.
"""

from __future__ import annotations

import inspect

import pytest
from llm_support import FakeAiMessage, FakeLangChainClient, FakeProviderError

from app.llm import providers
from app.llm.errors import (
    ModelCredentialsMissing,
    ModelRateLimited,
    ModelUnsupportedCapability,
    UnknownModel,
)
from app.llm.pricing import DEEPSEEK_V4_FLASH_PRICE
from app.llm.providers import (
    ChatMessage,
    CompletionRequest,
    LangChainChatModel,
    MessageRole,
    ModelCapability,
    system_message,
    user_message,
)
from app.llm.providers.deepseek import (
    DEEPSEEK_API_KEY_ENV,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_CHAT,
    DEEPSEEK_REASONER,
    DEFAULT_MODEL,
    MODEL_SPECS,
    DeepSeekSettings,
    build_deepseek_chat_model,
    spec_for,
)
from app.llm.usage import TokenUsage

API_KEY = "sk-not-a-real-key"


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEEPSEEK_API_KEY_ENV, raising=False)


def settings(**overrides: object) -> DeepSeekSettings:
    """Build settings without reading the developer's own ``.env``."""
    return DeepSeekSettings(_env_file=None, **overrides)  # type: ignore[call-arg]


class TestModelRegistry:
    def test_the_default_model_is_the_one_that_can_be_constrained_to_json(self) -> None:
        assert DEFAULT_MODEL == DEEPSEEK_CHAT.name
        assert DEEPSEEK_CHAT.supports(ModelCapability.STRUCTURED_OUTPUT)
        assert DEEPSEEK_CHAT.supports(ModelCapability.TOOL_CALLING)
        assert DEEPSEEK_CHAT.supports(ModelCapability.JSON_MODE)

    def test_the_reasoning_model_declares_that_it_cannot_do_structured_output(self) -> None:
        assert not DEEPSEEK_REASONER.supports(ModelCapability.STRUCTURED_OUTPUT)
        assert not DEEPSEEK_REASONER.supports(ModelCapability.TOOL_CALLING)
        assert DEEPSEEK_REASONER.supports(ModelCapability.THINKING)

    def test_requiring_a_missing_capability_raises_a_blocking_error(self) -> None:
        with pytest.raises(ModelUnsupportedCapability):
            DEEPSEEK_REASONER.require(ModelCapability.STRUCTURED_OUTPUT)

    def test_every_registered_model_carries_a_price_snapshot(self) -> None:
        assert MODEL_SPECS
        for spec in MODEL_SPECS.values():
            assert spec.provider == "deepseek"
            assert spec.price.output_per_million > 0

    def test_the_chat_alias_is_billed_as_the_model_it_aliases(self) -> None:
        assert DEEPSEEK_CHAT.price == DEEPSEEK_V4_FLASH_PRICE

    def test_an_unregistered_model_name_is_refused(self) -> None:
        with pytest.raises(UnknownModel):
            spec_for("gpt-nonexistent")

    def test_a_registered_name_resolves_to_its_spec(self) -> None:
        assert spec_for("deepseek-chat") is DEEPSEEK_CHAT


class TestSettings:
    def test_the_key_is_read_from_the_environment(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, API_KEY)

        assert settings().api_key is not None
        assert settings().api_key.get_secret_value() == API_KEY

    def test_the_key_is_not_printable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, API_KEY)

        assert API_KEY not in repr(settings())
        assert API_KEY not in str(settings())

    def test_the_defaults_point_at_the_official_endpoint(self, clean_env: None) -> None:
        configured = settings()

        assert configured.base_url == DEEPSEEK_BASE_URL
        assert configured.chat_model == DEFAULT_MODEL
        assert configured.timeout_seconds > 0

    def test_the_sdk_does_not_retry_because_this_layer_owns_retries(
        self,
        clean_env: None,
    ) -> None:
        """Hidden SDK retries would spend tokens the run never records."""
        assert settings().transport_retries == 0

    def test_no_key_material_is_baked_into_the_source(self) -> None:
        source = inspect.getsource(providers.deepseek)

        assert "sk-" not in source
        assert DEEPSEEK_API_KEY_ENV in source


class TestModelConstruction:
    def test_a_missing_key_is_a_blocking_configuration_error(self, clean_env: None) -> None:
        with pytest.raises(ModelCredentialsMissing) as failure:
            build_deepseek_chat_model(settings=settings())

        assert DEEPSEEK_API_KEY_ENV in str(failure.value)

    def test_the_client_is_configured_from_settings_only(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, API_KEY)
        captured: dict[str, object] = {}

        def factory(**kwargs: object) -> FakeLangChainClient:
            captured.update(kwargs)
            return FakeLangChainClient([])

        model = build_deepseek_chat_model(settings=settings(), client_factory=factory)

        assert model.spec is DEEPSEEK_CHAT
        assert captured["model"] == DEFAULT_MODEL
        assert captured["base_url"] == DEEPSEEK_BASE_URL
        assert captured["max_retries"] == 0
        assert captured["api_key"] == API_KEY
        assert captured["timeout"] == settings().timeout_seconds

    def test_an_explicit_model_name_overrides_the_default(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, API_KEY)

        model = build_deepseek_chat_model(
            settings=settings(),
            model="deepseek-reasoner",
            client_factory=lambda **kwargs: FakeLangChainClient([]),
        )

        assert model.spec is DEEPSEEK_REASONER

    def test_an_unknown_model_name_is_refused_before_a_client_exists(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(DEEPSEEK_API_KEY_ENV, API_KEY)

        with pytest.raises(UnknownModel):
            build_deepseek_chat_model(settings=settings(), model="deepseek-imaginary")


def request_for(schema: dict[str, object] | None = None, **overrides: object) -> CompletionRequest:
    payload: dict[str, object] = {
        "messages": (system_message("Only use the evidence."), user_message("Write it.")),
        "response_schema": schema,
    }
    payload.update(overrides)
    return CompletionRequest(**payload)  # type: ignore[arg-type]


class TestLangChainAdapter:
    def test_messages_are_translated_to_the_langchain_roles(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("done")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        model.complete(request_for())

        assert client.invocations[0] == [
            ("system", "Only use the evidence."),
            ("human", "Write it."),
        ]

    def test_an_assistant_turn_survives_the_translation(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("done")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        model.complete(
            request_for(messages=(ChatMessage(role=MessageRole.ASSISTANT, content="{}"),))
        )

        assert client.invocations[0] == [("ai", "{}")]

    def test_a_schema_request_switches_the_provider_into_json_mode(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("{}")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        model.complete(request_for({"type": "object"}))

        assert {"type": "json_object"} in [bound.get("response_format") for bound in client.bound]

    def test_a_plain_text_request_does_not_ask_for_json(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("prose")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        model.complete(request_for())

        assert all("response_format" not in bound for bound in client.bound)

    def test_the_text_of_a_string_content_message_is_returned(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("hello")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        assert model.complete(request_for()).text == "hello"

    def test_content_blocks_are_concatenated(self) -> None:
        """LangChain v1 messages may carry a list of content blocks instead of a string."""
        client = FakeLangChainClient(
            [
                FakeAiMessage(
                    [{"type": "text", "text": "part one "}, {"type": "text", "text": "two"}]
                )
            ]
        )
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        assert model.complete(request_for()).text == "part one two"

    def test_usage_is_taken_from_the_provider_metadata(self) -> None:
        client = FakeLangChainClient(
            [
                FakeAiMessage(
                    "{}",
                    usage_metadata={
                        "input_tokens": 300,
                        "output_tokens": 40,
                        "input_token_details": {"cache_read": 250},
                    },
                    response_metadata={"finish_reason": "stop"},
                )
            ]
        )
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        completion = model.complete(request_for())

        assert completion.usage == TokenUsage(
            input_tokens=300, output_tokens=40, cached_input_tokens=250
        )
        assert completion.finish_reason == "stop"
        assert completion.model == DEEPSEEK_CHAT.name

    def test_an_empty_reply_is_reported_rather_than_hidden(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        assert model.complete(request_for()).is_empty

    def test_a_provider_failure_is_translated_into_a_classified_error(self) -> None:
        client = FakeLangChainClient([FakeProviderError("429 too many requests", 429)])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        with pytest.raises(ModelRateLimited):
            model.complete(request_for())

    def test_the_configured_key_is_scrubbed_from_provider_failures(self) -> None:
        client = FakeLangChainClient([FakeProviderError(f"401 bad key {API_KEY}", 401)])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT, secret=API_KEY)

        with pytest.raises(Exception) as failure:
            model.complete(request_for())

        assert API_KEY not in str(failure.value)

    def test_per_request_generation_limits_are_passed_through(self) -> None:
        client = FakeLangChainClient([FakeAiMessage("{}")])
        model = LangChainChatModel(client, spec=DEEPSEEK_CHAT)

        model.complete(request_for(temperature=0.2, max_output_tokens=512, timeout=30.0))

        bound = client.bound[0]
        assert bound["temperature"] == 0.2
        assert bound["max_tokens"] == 512
        assert bound["timeout"] == 30.0
