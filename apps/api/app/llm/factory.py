"""Pick the configured chat model.

A custom ``LLM_BASE_URL`` / ``LLM_API_KEY`` pair is an OpenAI-compatible gateway.
Otherwise the DeepSeek factory is used.
"""

from __future__ import annotations

from app.llm.errors import ModelCredentialsMissing
from app.llm.providers.base import ChatModel
from app.llm.providers.deepseek import DeepSeekSettings, build_deepseek_chat_model
from app.llm.providers.openai_compatible import (
    OpenAICompatibleSettings,
    build_openai_compatible_chat_model,
)


def build_chat_model() -> ChatModel:
    """Return the deployment's chat model, or explain what is missing."""
    compatible = OpenAICompatibleSettings()
    key = compatible.api_key.get_secret_value().strip() if compatible.api_key else ""
    if key or compatible.base_url:
        return build_openai_compatible_chat_model(settings=compatible)
    deepseek = DeepSeekSettings()
    if deepseek.api_key:
        return build_deepseek_chat_model(settings=deepseek)
    raise ModelCredentialsMissing(
        "no chat credentials: set LLM_API_KEY and LLM_BASE_URL, or DEEPSEEK_API_KEY"
    )
