"""Offline doubles for the model layer.

Every test in this phase drives a scripted fake instead of a real provider: the model
contract is a narrow port precisely so the retry, repair and accounting behaviour can be
proven without an API key, a network or a bill.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from datetime import date
from decimal import Decimal
from typing import Any

from app.llm.pricing import PriceSnapshot
from app.llm.providers import (
    ChatMessage,
    Completion,
    CompletionRequest,
    ModelCapability,
    ModelSpec,
)
from app.llm.usage import TokenUsage

#: Deliberately round numbers so expected costs in tests are readable by hand.
TEST_PRICE = PriceSnapshot(
    model="fake-model",
    input_cache_miss_per_million=Decimal("1.00"),
    input_cache_hit_per_million=Decimal("0.10"),
    output_per_million=Decimal("2.00"),
    captured_on=date(2026, 1, 1),
    source="tests",
)

STRUCTURED_SPEC = ModelSpec(
    name="fake-model",
    provider="fake",
    price=TEST_PRICE,
    capabilities=frozenset(
        {
            ModelCapability.JSON_MODE,
            ModelCapability.STRUCTURED_OUTPUT,
            ModelCapability.TOOL_CALLING,
        }
    ),
    context_tokens=8192,
    max_output_tokens=2048,
)

#: Stands in for ``deepseek-reasoner``: a model that cannot be asked for JSON.
TEXT_ONLY_SPEC = ModelSpec(
    name="fake-reasoner",
    provider="fake",
    price=TEST_PRICE,
    capabilities=frozenset({ModelCapability.THINKING}),
    context_tokens=8192,
    max_output_tokens=2048,
)

DEFAULT_USAGE = TokenUsage(input_tokens=100, output_tokens=50)


def reply(text: str, *, usage: TokenUsage | None = None, finish_reason: str = "stop") -> Completion:
    return Completion(
        text=text,
        usage=usage or DEFAULT_USAGE,
        model=STRUCTURED_SPEC.name,
        finish_reason=finish_reason,
    )


class FakeChatModel:
    """Replays a script of completions and provider failures, in order.

    A script entry is either a :class:`Completion` to return or an exception to raise, so
    one fake covers the happy path, transport failures and malformed output alike.
    """

    def __init__(
        self,
        script: Iterable[Completion | BaseException],
        *,
        spec: ModelSpec = STRUCTURED_SPEC,
    ) -> None:
        self._script: deque[Completion | BaseException] = deque(script)
        self._spec = spec
        self.requests: list[CompletionRequest] = []

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def messages(self) -> list[ChatMessage]:
        """Every message sent across every call, for prompt-assembly assertions."""
        return [message for request in self.requests for message in request.messages]

    def complete(self, request: CompletionRequest) -> Completion:
        self.requests.append(request)
        if not self._script:
            raise AssertionError(f"the fake model was called {self.calls} times with no reply left")
        item = self._script.popleft()
        if isinstance(item, BaseException):
            raise item
        return item


class RecordingSleeper:
    """Captures backoff delays instead of spending them."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeProviderError(Exception):
    """Shaped like an HTTP error from an OpenAI-compatible SDK."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class FakeAiMessage:
    """The parts of a LangChain ``AIMessage`` the adapter is allowed to rely on."""

    def __init__(
        self,
        content: Any,
        *,
        usage_metadata: dict[str, Any] | None = None,
        response_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.content = content
        self.usage_metadata = usage_metadata
        self.response_metadata = response_metadata or {}


class FakeLangChainClient:
    """A stand-in for ``ChatDeepSeek`` that records binds and invocations."""

    def __init__(self, replies: Iterable[Any], **kwargs: Any) -> None:
        self._replies: deque[Any] = deque(replies)
        self.kwargs = kwargs
        self.bound: list[dict[str, Any]] = []
        self.invocations: list[Any] = []

    def bind(self, **kwargs: Any) -> FakeLangChainClient:
        self.bound.append(kwargs)
        return self

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        self.invocations.append(messages)
        item = self._replies.popleft()
        if isinstance(item, BaseException):
            raise item
        return item
