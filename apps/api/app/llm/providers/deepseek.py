"""DeepSeek, the default provider, through the official ``langchain-deepseek`` package.

The registry below is the load-bearing part. ``deepseek-chat`` and ``deepseek-reasoner`` are
compatibility aliases the provider kept for the non-thinking and thinking modes of one
model, and only the non-thinking one can be constrained to JSON or tool calls. Recording
that as a capability set means a caller that needs structured output is refused up front
instead of receiving prose that fails validation three times over.

Credentials come from the server's environment and nowhere else. There is no key in this
repository, no key in a prompt, and the adapter scrubs the configured key out of provider
error messages before they reach a log or a run record.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Final

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.errors import ModelCredentialsMissing, ModelProviderUnavailable, UnknownModel
from app.llm.pricing import DEEPSEEK_V4_FLASH_PRICE, DEEPSEEK_V4_PRO_PRICE
from app.llm.providers.base import (
    DEFAULT_TIMEOUT_SECONDS,
    ModelCapability,
    ModelSpec,
)
from app.llm.providers.langchain_chat import LangChainChatModel
from app.settings import REPO_ROOT

PROVIDER: Final = "deepseek"

DEEPSEEK_BASE_URL: Final = "https://api.deepseek.com"

#: The variable the official package reads, and the only place a key may come from.
DEEPSEEK_API_KEY_ENV: Final = "DEEPSEEK_API_KEY"

#: The non-thinking alias: the one DeepSeek model that supports JSON output and tools.
DEFAULT_MODEL: Final = "deepseek-chat"

_PACKAGE: Final = "langchain-deepseek"

_CONSTRAINABLE: Final = frozenset(
    {
        ModelCapability.JSON_MODE,
        ModelCapability.STRUCTURED_OUTPUT,
        ModelCapability.TOOL_CALLING,
    }
)

#: The published context and output ceilings for the V4 family. Generation caps its own
#: output far below this; the numbers are here so a caller can size an evidence pack.
_CONTEXT_TOKENS: Final = 1_000_000
_MAX_OUTPUT_TOKENS: Final = 384_000


DEEPSEEK_CHAT: Final = ModelSpec(
    name=DEFAULT_MODEL,
    provider=PROVIDER,
    price=DEEPSEEK_V4_FLASH_PRICE,
    capabilities=_CONSTRAINABLE,
    context_tokens=_CONTEXT_TOKENS,
    max_output_tokens=_MAX_OUTPUT_TOKENS,
)

DEEPSEEK_REASONER: Final = ModelSpec(
    name="deepseek-reasoner",
    provider=PROVIDER,
    price=DEEPSEEK_V4_FLASH_PRICE,
    capabilities=frozenset({ModelCapability.THINKING}),
    context_tokens=_CONTEXT_TOKENS,
    max_output_tokens=_MAX_OUTPUT_TOKENS,
)

DEEPSEEK_V4_FLASH: Final = ModelSpec(
    name="deepseek-v4-flash",
    provider=PROVIDER,
    price=DEEPSEEK_V4_FLASH_PRICE,
    capabilities=_CONSTRAINABLE | {ModelCapability.THINKING},
    context_tokens=_CONTEXT_TOKENS,
    max_output_tokens=_MAX_OUTPUT_TOKENS,
)

DEEPSEEK_V4_PRO: Final = ModelSpec(
    name="deepseek-v4-pro",
    provider=PROVIDER,
    price=DEEPSEEK_V4_PRO_PRICE,
    capabilities=_CONSTRAINABLE | {ModelCapability.THINKING},
    context_tokens=_CONTEXT_TOKENS,
    max_output_tokens=_MAX_OUTPUT_TOKENS,
)

MODEL_SPECS: Final[Mapping[str, ModelSpec]] = {
    spec.name: spec
    for spec in (DEEPSEEK_CHAT, DEEPSEEK_REASONER, DEEPSEEK_V4_FLASH, DEEPSEEK_V4_PRO)
}


def spec_for(model: str) -> ModelSpec:
    """Return the registered spec for ``model``.

    An unregistered name is refused rather than assumed capable: guessing would trade a
    clear configuration error for a mid-run failure with a confusing message.
    """
    spec = MODEL_SPECS.get(model)
    if spec is None:
        known = ", ".join(sorted(MODEL_SPECS))
        raise UnknownModel(f"unknown DeepSeek model {model!r}; registered models are {known}")
    return spec


class DeepSeekSettings(BaseSettings):
    """Server-side model configuration, read from the environment or the root ``.env``."""

    api_key: SecretStr | None = None
    base_url: str = DEEPSEEK_BASE_URL
    chat_model: str = DEFAULT_MODEL
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    # The SDK's own retries would spend tokens this deployment never records and would
    # stack on top of the generation layer's backoff, so they are off by default.
    transport_retries: int = 0

    model_config = SettingsConfigDict(
        env_prefix="deepseek_",
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


def build_deepseek_chat_model(
    *,
    settings: DeepSeekSettings | None = None,
    model: str | None = None,
    client_factory: Callable[..., Any] | None = None,
) -> LangChainChatModel:
    """Return a ready model, or explain what configuration is missing.

    ``client_factory`` exists so the wiring can be asserted without constructing a real
    client; production passes nothing and gets ``ChatDeepSeek``.
    """
    configured = settings or DeepSeekSettings()
    spec = spec_for(model or configured.chat_model)
    key = _required_key(configured)
    factory = client_factory or _chat_deepseek
    client = factory(
        model=spec.name,
        api_key=key,
        base_url=configured.base_url,
        timeout=configured.timeout_seconds,
        max_retries=configured.transport_retries,
    )
    return LangChainChatModel(client, spec=spec, secret=key)


def _required_key(settings: DeepSeekSettings) -> str:
    key = settings.api_key.get_secret_value().strip() if settings.api_key else ""
    if not key:
        raise ModelCredentialsMissing(
            f"no DeepSeek credentials: set {DEEPSEEK_API_KEY_ENV} in the server environment "
            "or the deployment's .env file"
        )
    return key


def _chat_deepseek(**kwargs: Any) -> Any:
    """Import the provider lazily so nothing else has to depend on it being installed."""
    try:
        from langchain_deepseek import ChatDeepSeek
    except ModuleNotFoundError as error:  # pragma: no cover - deployment configuration
        raise ModelProviderUnavailable(
            f"{_PACKAGE} is not installed in this environment"
        ) from error
    return ChatDeepSeek(**kwargs)
