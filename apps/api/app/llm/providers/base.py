"""The model contract every generation step talks to.

Deliberately narrower than any SDK: one method, plain data in and out. That is what makes
the model pluggable (DeepSeek today, anything else later), keeps every retry, repair and
cost rule in one place instead of spread across provider code, and lets the whole
generation phase be tested with a scripted fake and no network.

A model also has to declare what it can do. DeepSeek's reasoning model cannot be
constrained to JSON, so a caller that needs structured output must be refused before a
request is built rather than after a chapter comes back as prose.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from app.llm.errors import ModelUnsupportedCapability
from app.llm.pricing import PriceSnapshot
from app.llm.usage import TokenUsage

#: Generous enough for a long chapter, short enough to fit inside a step lease.
DEFAULT_TIMEOUT_SECONDS: float = 120.0


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


@dataclass(frozen=True, slots=True)
class ChatMessage:
    role: MessageRole
    content: str


def system_message(content: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.SYSTEM, content=content)


def user_message(content: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.USER, content=content)


def assistant_message(content: str) -> ChatMessage:
    return ChatMessage(role=MessageRole.ASSISTANT, content=content)


class ModelCapability(StrEnum):
    """What a model can be asked to do, as opposed to what a prompt asks of it."""

    JSON_MODE = "json_mode"
    STRUCTURED_OUTPUT = "structured_output"
    TOOL_CALLING = "tool_calling"
    THINKING = "thinking"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """A model, its declared capabilities and the price snapshot it is billed against."""

    name: str
    provider: str
    price: PriceSnapshot
    capabilities: frozenset[ModelCapability]
    context_tokens: int
    max_output_tokens: int

    def supports(self, capability: ModelCapability) -> bool:
        return capability in self.capabilities

    def require(self, capability: ModelCapability) -> None:
        if not self.supports(capability):
            raise ModelUnsupportedCapability(
                f"{self.name} does not support {capability.value}; choose another model"
            )


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    """One prompt plus the constraints the provider should apply to the reply.

    ``response_schema`` being set means "the reply must be a single JSON object matching
    this schema"; providers translate it into whatever their JSON mode is called.
    ``timeout`` of ``None`` leaves the model's configured timeout alone.
    """

    messages: tuple[ChatMessage, ...]
    response_schema: Mapping[str, Any] | None = None
    schema_name: str = ""
    temperature: float = 0.0
    max_output_tokens: int | None = None
    timeout: float | None = None

    def prompt_hash(self) -> str:
        """Return a stable digest of everything that shapes the reply.

        Stored on the step as ``prompt_hash`` so a regenerated chapter can be told apart
        from a re-run of the identical prompt.
        """
        canonical = json.dumps(
            {
                "messages": [
                    {"role": message.role.value, "content": message.content}
                    for message in self.messages
                ],
                "schema": self.response_schema,
                "schema_name": self.schema_name,
                "temperature": self.temperature,
                "max_output_tokens": self.max_output_tokens,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Completion:
    """What a provider returned, with the accounting attached."""

    text: str
    usage: TokenUsage
    model: str
    finish_reason: str | None = None

    @property
    def is_empty(self) -> bool:
        """DeepSeek's JSON mode occasionally returns no content at all."""
        return not self.text.strip()


@runtime_checkable
class ChatModel(Protocol):
    """The only model surface generation code is allowed to depend on."""

    @property
    def spec(self) -> ModelSpec: ...

    def complete(self, request: CompletionRequest) -> Completion: ...
