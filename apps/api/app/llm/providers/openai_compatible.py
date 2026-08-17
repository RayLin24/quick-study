"""Any OpenAI-compatible chat gateway: custom base URL, model name and key.

DeepSeek is the default provider in this repository. A self-hosted or proxied endpoint
(GLM, vLLM, One-API, …) is the same wire protocol with a different origin, so it gets its
own factory rather than being forced through the DeepSeek model registry.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Final
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.errors import (
    ModelConnectionError,
    ModelCredentialsMissing,
    ModelTimeout,
    classify_provider_error,
)
from app.llm.pricing import PriceSnapshot
from app.llm.providers.base import (
    DEFAULT_TIMEOUT_SECONDS,
    Completion,
    CompletionRequest,
    ModelCapability,
    ModelSpec,
)
from app.llm.usage import TokenUsage
from app.settings import REPO_ROOT

PROVIDER: Final = "openai-compatible"
LLM_API_KEY_ENV: Final = "LLM_API_KEY"
DEFAULT_MODEL: Final = "glm-5.3"

_CONSTRAINABLE: Final = frozenset(
    {
        ModelCapability.JSON_MODE,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.TOOL_CALLING,
    }
)

#: Local / proxied gateways are not billed through DeepSeek's rate card. Record zero so a
#: run still has a reproducible cost rather than failing as "unpriced".
UNMETERED_PRICE: Final = PriceSnapshot(
    model="openai-compatible",
    input_cache_miss_per_million=Decimal("0"),
    input_cache_hit_per_million=Decimal("0"),
    output_per_million=Decimal("0"),
    captured_on=date(2026, 8, 17),
    source="openai-compatible-gateway",
)


class OpenAICompatibleSettings(BaseSettings):
    api_key: SecretStr | None = None
    base_url: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    model_config = SettingsConfigDict(
        env_prefix="llm_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class OpenAICompatibleChatModel:
    """POST ``/v1/chat/completions`` and speak this package's ``ChatModel`` port."""

    def __init__(
        self,
        *,
        spec: ModelSpec,
        api_key: str,
        base_url: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.Client | None = None,
    ) -> None:
        self._spec = spec
        self._api_key = api_key
        self._url = chat_completions_url(base_url)
        self._timeout = timeout_seconds
        self._client = client

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    def complete(self, request: CompletionRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": self._spec.name,
            "messages": [
                {"role": message.role.value, "content": message.content}
                for message in request.messages
            ],
            "temperature": request.temperature,
        }
        if request.max_output_tokens is not None:
            payload["max_tokens"] = request.max_output_tokens
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = self._post(payload, headers, request.timeout)
        except httpx.TimeoutException as error:
            raise ModelTimeout("the chat gateway timed out") from error
        except httpx.ConnectError as error:
            raise ModelConnectionError("could not reach the chat gateway") from error
        except httpx.HTTPError as error:
            raise classify_provider_error(error, secret=self._api_key) from error

        if response.status_code >= 400:
            raise classify_provider_error(
                _HttpStatus(response.status_code, _redact(response.text, self._api_key)),
                secret=self._api_key,
            )

        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        text = _message_text(message)
        return Completion(
            text=text,
            usage=_usage(body.get("usage")),
            model=self._spec.name,
            finish_reason=choice.get("finish_reason"),
        )

    def _post(
        self, payload: dict[str, Any], headers: dict[str, str], timeout: float | None
    ) -> httpx.Response:
        client = self._client or httpx.Client(timeout=timeout or self._timeout)
        close = self._client is None
        try:
            return client.post(self._url, json=payload, headers=headers)
        finally:
            if close:
                client.close()


def build_openai_compatible_chat_model(
    *,
    settings: OpenAICompatibleSettings | None = None,
    model: str | None = None,
    client: httpx.Client | None = None,
) -> OpenAICompatibleChatModel:
    configured = settings or OpenAICompatibleSettings()
    key = configured.api_key.get_secret_value().strip() if configured.api_key else ""
    if not key:
        raise ModelCredentialsMissing(
            f"no chat credentials: set {LLM_API_KEY_ENV} in the server environment or .env"
        )
    name = model or configured.model
    spec = ModelSpec(
        name=name,
        provider=PROVIDER,
        price=UNMETERED_PRICE,
        capabilities=_CONSTRAINABLE,
        context_tokens=128_000,
        max_output_tokens=16_384,
    )
    return OpenAICompatibleChatModel(
        spec=spec,
        api_key=key,
        base_url=configured.base_url,
        timeout_seconds=configured.timeout_seconds,
        client=client,
    )


def chat_completions_url(base_url: str) -> str:
    """Return the chat-completions URL for a gateway root or a /v1 prefix."""
    root = (base_url or "http://127.0.0.1").rstrip("/")
    path = urlparse(root).path.rstrip("/")
    if path.endswith("/chat/completions"):
        return root
    if path.endswith("/v1"):
        return f"{root}/chat/completions"
    return f"{root}/v1/chat/completions"


class _HttpStatus(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str) and part.strip():
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text") or part.get("content")
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        if parts:
            return "".join(parts)
    reasoning = message.get("reasoning_content") or message.get("reasoning")
    if isinstance(reasoning, str) and reasoning.strip():
        return reasoning
    return content if isinstance(content, str) else ""


def _usage(payload: Any) -> TokenUsage:
    if not isinstance(payload, dict):
        return TokenUsage()
    return TokenUsage(
        input_tokens=int(payload.get("prompt_tokens") or 0),
        output_tokens=int(payload.get("completion_tokens") or 0),
    )


def _redact(text: str, secret: str) -> str:
    return text.replace(secret, "[redacted]") if secret else text
