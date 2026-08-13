"""Failures of a model call, split by what an operator or a worker should do about them.

Two branches only, because that is the decision a caller has to make. A
:class:`RetryableModelError` is a transient upstream condition -- a rate limit, an overloaded
server, a dropped connection -- and waiting helps. A :class:`BlockingModelError` will fail
identically on every attempt: a malformed request, a bad key, an empty balance, output that
never validates. Retrying those wastes the run's attempt budget and, worse, hides the one
thing an operator can actually fix.

``code`` is the short, stable string persisted in ``steps.error_code``; message text is for
humans and is never parsed.
"""

from __future__ import annotations

from typing import Any, ClassVar, Final

#: Statuses DeepSeek documents as transient: 429 rate limit, 500 server fault,
#: 503 overloaded, plus the gateway and timeout statuses any proxy in front can emit.
_RETRYABLE_STATUSES: Final = frozenset({408, 425, 429, 500, 502, 503, 504})

_SECRET_PLACEHOLDER: Final = "[redacted]"  # noqa: S105 - scrub marker, not a credential


class ModelError(Exception):
    """Base class for every model call failure."""

    code: ClassVar[str] = "model_error"

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class RetryableModelError(ModelError):
    """A transient upstream condition; the same request may succeed later."""

    code: ClassVar[str] = "model_retryable"


class BlockingModelError(ModelError):
    """A condition that will not change on retry; stop and surface it."""

    code: ClassVar[str] = "model_blocked"


class ModelTimeout(RetryableModelError):
    code: ClassVar[str] = "timeout"


class ModelConnectionError(RetryableModelError):
    code: ClassVar[str] = "connection_error"


class ModelServerError(RetryableModelError):
    code: ClassVar[str] = "server_error"


class ModelRateLimited(RetryableModelError):
    """The provider asked us to slow down, sometimes with a ``Retry-After`` hint."""

    code: ClassVar[str] = "rate_limited"

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after


class ModelInvalidRequest(BlockingModelError):
    """The provider rejected the request itself (400, 404, 422)."""

    code: ClassVar[str] = "invalid_request"


class ModelAuthenticationError(BlockingModelError):
    """The configured key is missing, wrong or not permitted (401, 403)."""

    code: ClassVar[str] = "authentication_failed"


class ModelQuotaExhausted(BlockingModelError):
    """DeepSeek returns 402 when the account balance is empty; only a top-up fixes it."""

    code: ClassVar[str] = "quota_exhausted"


class ModelCredentialsMissing(BlockingModelError):
    """No API key was configured, so no call was attempted."""

    code: ClassVar[str] = "credentials_missing"


class ModelProviderUnavailable(BlockingModelError):
    """The provider package is not installed in this deployment."""

    code: ClassVar[str] = "provider_unavailable"


class ModelUnsupportedCapability(BlockingModelError):
    """The chosen model cannot do what the caller asked for, whatever the prompt says."""

    code: ClassVar[str] = "unsupported_capability"


class UnknownModel(BlockingModelError):
    """A model name that is not in the registry, so its capabilities are unknown."""

    code: ClassVar[str] = "unknown_model"


class UnknownModelPrice(BlockingModelError):
    """A model with no price snapshot; billing it as free would understate the run's cost."""

    code: ClassVar[str] = "unknown_model_price"


class CostBudgetExceeded(BlockingModelError):
    """The run spent its configured budget; continuing would spend more."""

    code: ClassVar[str] = "cost_budget_exceeded"


class ProviderCallFailed(BlockingModelError):
    """An unrecognised failure. Blocking on purpose: an unknown fault is not a rate limit,
    and retrying it three times only multiplies whatever went wrong."""

    code: ClassVar[str] = "provider_call_failed"


class StructuredOutputInvalid(BlockingModelError):
    """The model never produced output matching the schema, even after repairs."""

    code: ClassVar[str] = "structured_output_invalid"

    def __init__(self, message: str, *, raw_text: str = "", repairs: int = 0) -> None:
        super().__init__(message)
        self.raw_text = raw_text
        self.repairs = repairs


def is_retryable(error: BaseException) -> bool:
    return isinstance(error, RetryableModelError)


def error_for_status(
    status: int,
    message: str,
    *,
    retry_after: float | None = None,
) -> ModelError:
    """Return the error class that matches an HTTP status from the provider."""
    if status == 429:
        return ModelRateLimited(message, status_code=status, retry_after=retry_after)
    if status == 408:
        return ModelTimeout(message, status_code=status)
    if status in _RETRYABLE_STATUSES:
        return ModelServerError(message, status_code=status)
    if status in (401, 403):
        return ModelAuthenticationError(message, status_code=status)
    if status == 402:
        return ModelQuotaExhausted(message, status_code=status)
    if status in (400, 404, 422):
        return ModelInvalidRequest(message, status_code=status)
    if 500 <= status < 600:
        return ModelServerError(message, status_code=status)
    return ProviderCallFailed(f"unexpected HTTP {status}: {message}", status_code=status)


def classify_provider_error(error: BaseException, *, secret: str | None = None) -> ModelError:
    """Translate an SDK exception into this module's vocabulary.

    The provider SDK is not imported here: statuses are read structurally, so the mapping
    works for the OpenAI-compatible client ``langchain-deepseek`` uses and for anything
    else that reports a status the same way. ``secret`` is scrubbed from the message
    because SDK errors quote the failing request, headers included.
    """
    if isinstance(error, ModelError):
        return error

    message = _redact(str(error) or type(error).__name__, secret)
    status = _status_of(error)
    if status is not None:
        return error_for_status(status, message, retry_after=_retry_after_of(error))

    names = " ".join(base.__name__ for base in type(error).__mro__)
    if "Timeout" in names:
        return ModelTimeout(message)
    if "Connection" in names or "Connect" in names:
        return ModelConnectionError(message)
    return ProviderCallFailed(f"{type(error).__name__}: {message}")


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, _SECRET_PLACEHOLDER)


def _status_of(error: BaseException) -> int | None:
    for attribute in ("status_code", "http_status", "status"):
        status = _as_status(getattr(error, attribute, None))
        if status is not None:
            return status
    response = getattr(error, "response", None)
    if response is not None:
        for attribute in ("status_code", "status"):
            status = _as_status(getattr(response, attribute, None))
            if status is not None:
                return status
    return None


def _as_status(value: Any) -> int | None:
    return value if isinstance(value, int) and 100 <= value < 600 else None


def _retry_after_of(error: BaseException) -> float | None:
    hint = getattr(error, "retry_after", None)
    if hint is None:
        headers = getattr(getattr(error, "response", None), "headers", None)
        hint = headers.get("retry-after") if hasattr(headers, "get") else None
    try:
        return float(hint) if hint is not None else None
    except (TypeError, ValueError):
        return None
