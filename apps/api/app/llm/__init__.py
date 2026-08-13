"""Model access for the generation phase.

Callers see three things: a :class:`~app.llm.providers.base.ChatModel` port, the
:func:`~app.llm.structured.generate_structured` entry point that is the only sanctioned way
to get a value out of a model, and a :class:`~app.llm.usage.UsageLedger` that says what it
cost. Providers, retries, repairs, error classification and pricing all sit behind those.
"""

from app.llm.errors import (
    BlockingModelError,
    ModelError,
    RetryableModelError,
    StructuredOutputInvalid,
    is_retryable,
)
from app.llm.pricing import PriceSnapshot, resolve_price
from app.llm.providers import (
    ChatMessage,
    ChatModel,
    Completion,
    CompletionRequest,
    MessageRole,
    ModelCapability,
    ModelSpec,
    assistant_message,
    build_deepseek_chat_model,
    system_message,
    user_message,
)
from app.llm.structured import (
    RetryPolicy,
    StructuredResult,
    generate_structured,
)
from app.llm.usage import CallOutcome, ModelCall, TokenUsage, UsageLedger

__all__ = [
    "BlockingModelError",
    "CallOutcome",
    "ChatMessage",
    "ChatModel",
    "Completion",
    "CompletionRequest",
    "MessageRole",
    "ModelCall",
    "ModelCapability",
    "ModelError",
    "ModelSpec",
    "PriceSnapshot",
    "RetryPolicy",
    "RetryableModelError",
    "StructuredOutputInvalid",
    "StructuredResult",
    "TokenUsage",
    "UsageLedger",
    "assistant_message",
    "build_deepseek_chat_model",
    "generate_structured",
    "is_retryable",
    "resolve_price",
    "system_message",
    "user_message",
]
