"""Model providers.

``base`` is the contract; ``langchain_chat`` bridges any LangChain chat model onto it;
``deepseek`` is the default provider and the model registry. Nothing outside this package
imports a provider SDK, so swapping the model is a change of factory, not of call sites.
"""

from app.llm.providers import deepseek
from app.llm.providers.base import (
    DEFAULT_TIMEOUT_SECONDS,
    ChatMessage,
    ChatModel,
    Completion,
    CompletionRequest,
    MessageRole,
    ModelCapability,
    ModelSpec,
    assistant_message,
    system_message,
    user_message,
)
from app.llm.providers.deepseek import (
    DEFAULT_MODEL,
    DeepSeekSettings,
    build_deepseek_chat_model,
    spec_for,
)
from app.llm.providers.langchain_chat import LangChainChatModel

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_SECONDS",
    "ChatMessage",
    "ChatModel",
    "Completion",
    "CompletionRequest",
    "DeepSeekSettings",
    "LangChainChatModel",
    "MessageRole",
    "ModelCapability",
    "ModelSpec",
    "assistant_message",
    "build_deepseek_chat_model",
    "deepseek",
    "spec_for",
    "system_message",
    "user_message",
]
