"""Bridge from a LangChain chat model to this package's narrow port.

Nothing else in the codebase imports LangChain: the adapter is the seam. It translates
messages, turns a schema request into the provider's JSON mode, reads the usage the
provider reported and maps SDK exceptions into classified errors. It deliberately does not
retry -- retries, repairs and accounting all live one layer up, where they can be recorded.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from app.llm.errors import classify_provider_error
from app.llm.providers.base import Completion, CompletionRequest, MessageRole, ModelSpec
from app.llm.usage import TokenUsage

_LANGCHAIN_ROLES: Final[Mapping[MessageRole, str]] = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "human",
    MessageRole.ASSISTANT: "ai",
}

#: OpenAI-compatible JSON mode, which is what DeepSeek implements.
_JSON_RESPONSE_FORMAT: Final = {"type": "json_object"}


class LangChainChatModel:
    """Wraps any LangChain chat model, such as ``ChatDeepSeek``."""

    def __init__(self, client: Any, *, spec: ModelSpec, secret: str | None = None) -> None:
        self._client = client
        self._spec = spec
        self._secret = secret

    @property
    def spec(self) -> ModelSpec:
        return self._spec

    def complete(self, request: CompletionRequest) -> Completion:
        client = self._client.bind(**self._options(request))
        try:
            message = client.invoke(
                [
                    (_LANGCHAIN_ROLES[message.role], message.content)
                    for message in request.messages
                ]
            )
        except Exception as error:
            raise classify_provider_error(error, secret=self._secret) from error
        return Completion(
            text=message_text(message),
            usage=TokenUsage.from_metadata(getattr(message, "usage_metadata", None)),
            model=self._spec.name,
            finish_reason=_finish_reason(message),
        )

    def _options(self, request: CompletionRequest) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": request.temperature}
        if request.response_schema is not None:
            options["response_format"] = _JSON_RESPONSE_FORMAT
        if request.max_output_tokens is not None:
            options["max_tokens"] = request.max_output_tokens
        if request.timeout is not None:
            options["timeout"] = request.timeout
        return options


def message_text(message: Any) -> str:
    """Return the text of a reply, whatever shape the message carries it in.

    LangChain messages hold either a plain string or a list of content blocks, and newer
    versions expose a ``text`` accessor over both. All three shapes are handled here so a
    version bump cannot quietly turn a chapter into an empty string.
    """
    text = getattr(message, "text", None)
    if callable(text):
        text = text()
    if isinstance(text, str) and text:
        return text
    return _content_text(getattr(message, "content", ""))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence):
        return "".join(_block_text(block) for block in content)
    return ""


def _block_text(block: Any) -> str:
    if isinstance(block, str):
        return block
    if isinstance(block, Mapping):
        value = block.get("text", "")
        return value if isinstance(value, str) else ""
    value = getattr(block, "text", "")
    return value if isinstance(value, str) else ""


def _finish_reason(message: Any) -> str | None:
    metadata = getattr(message, "response_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    reason = metadata.get("finish_reason")
    return reason if isinstance(reason, str) else None
