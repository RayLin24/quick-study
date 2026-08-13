"""Validated structured generation: the only way this system calls a model.

Free-form text is never trusted into the pipeline. A caller names a Pydantic schema, and
this module is responsible for coming back with an instance of it or raising. Between those
two outcomes sit two very different recovery strategies:

* A transport fault (rate limit, overloaded server, dropped connection) is retried with
  exponential backoff, honouring a ``Retry-After`` hint but never sleeping past the cap,
  because a worker holding a step lease cannot afford an unbounded wait.
* A reply that is empty, unparseable or schema-invalid is repaired: the rejected text and
  the exact validation errors are sent back with a bounded number of re-prompts. DeepSeek's
  JSON mode is documented to return empty content occasionally, and a repair prompt is the
  documented mitigation.

Every attempt is recorded in the usage ledger, including the ones that produced nothing:
the provider charges for those, so the run has to account for them.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final

from pydantic import BaseModel, ValidationError

from app.llm.errors import StructuredOutputInvalid, classify_provider_error, is_retryable
from app.llm.providers.base import (
    ChatMessage,
    ChatModel,
    Completion,
    CompletionRequest,
    ModelCapability,
    assistant_message,
    user_message,
)
from app.llm.usage import CallOutcome, ModelCall, TokenUsage, UsageLedger

#: Two repairs is the point of diminishing returns: a model that has ignored the schema
#: twice is not one prompt away from complying, and each round costs a full call.
DEFAULT_MAX_REPAIRS: Final = 2

#: How much of a rejected reply is echoed back. Enough to locate the mistake, bounded so a
#: runaway reply cannot double the next prompt.
RAW_TEXT_ECHO_LIMIT: Final = 2_000

#: How many validation errors are listed in a repair prompt.
_MAX_REPORTED_ERRORS: Final = 8


class RejectionCode(StrEnum):
    """Why a reply was rejected, recorded on the ledger entry for that call."""

    EMPTY_RESPONSE = "empty_response"
    INVALID_JSON = "invalid_json"
    SCHEMA_VIOLATION = "schema_violation"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff for transport faults."""

    max_attempts: int = 3
    initial_backoff: float = 0.5
    multiplier: float = 2.0
    max_backoff: float = 8.0

    def delay_for(self, failures: int, retry_after: float | None = None) -> float:
        """Return how long to wait after ``failures`` consecutive transport faults.

        A provider's ``Retry-After`` wins over the computed backoff but is still capped:
        honouring a two-minute hint inside a five-minute step lease would hand the step to
        another worker mid-call.
        """
        backoff = self.initial_backoff * self.multiplier ** max(failures - 1, 0)
        if retry_after is not None:
            backoff = max(backoff, retry_after)
        return min(backoff, self.max_backoff)


DEFAULT_RETRY_POLICY: Final = RetryPolicy()


@dataclass(frozen=True, slots=True)
class StructuredResult[T]:
    """A validated value plus everything the run needs to record about producing it."""

    value: T
    model: str
    prompt_hash: str
    usage: TokenUsage
    cost_usd: Decimal
    attempts: int
    repairs: int
    calls: tuple[ModelCall, ...]


@dataclass(frozen=True, slots=True)
class _Rejection:
    code: RejectionCode
    instruction: str


def json_contract(schema: type[BaseModel]) -> str:
    """Return the instruction that pins the reply to ``schema``.

    The word "JSON" is not decoration: DeepSeek's JSON mode rejects a request whose prompt
    never mentions it, and including the schema itself is what makes a first-try match
    likely enough that repairs stay rare.
    """
    rendered = json.dumps(schema.model_json_schema(), ensure_ascii=False, indent=2, sort_keys=True)
    return (
        "Reply with a single JSON object and nothing else: no prose, no markdown fence.\n"
        "The JSON object must validate against this JSON Schema:\n"
        f"{rendered}"
    )


def generate_structured[T: BaseModel](
    model: ChatModel,
    *,
    schema: type[T],
    messages: Sequence[ChatMessage],
    policy: RetryPolicy = DEFAULT_RETRY_POLICY,
    max_repairs: int = DEFAULT_MAX_REPAIRS,
    temperature: float = 0.0,
    max_output_tokens: int | None = None,
    timeout: float | None = None,
    ledger: UsageLedger | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> StructuredResult[T]:
    """Call ``model`` until it returns a valid ``schema`` instance, or raise.

    Raises a :class:`~app.llm.errors.BlockingModelError` for anything a retry cannot fix,
    including output that never validated, and the last transport error when the retry
    budget runs out.
    """
    model.spec.require(ModelCapability.STRUCTURED_OUTPUT)
    book = ledger if ledger is not None else UsageLedger()
    prompt = tuple(messages) + (user_message(json_contract(schema)),)
    request = CompletionRequest(
        messages=prompt,
        response_schema=schema.model_json_schema(),
        schema_name=schema.__name__,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        timeout=timeout,
    )
    prompt_hash = request.prompt_hash()

    calls: list[ModelCall] = []
    transport_failures = 0
    repairs = 0
    rejected_text = ""

    while True:
        attempt = len(calls) + 1
        try:
            completion = model.complete(request)
        except Exception as error:
            classified = classify_provider_error(error)
            retrying = is_retryable(classified) and transport_failures + 1 < policy.max_attempts
            calls.append(
                _record(
                    book,
                    model,
                    attempt,
                    TokenUsage(),
                    CallOutcome.RETRIED if retrying else CallOutcome.FAILED,
                    classified.code,
                )
            )
            if not retrying:
                raise classified from error
            transport_failures += 1
            sleep(policy.delay_for(transport_failures, getattr(classified, "retry_after", None)))
            continue

        value, rejection = _validate(completion, schema)
        if rejection is None:
            calls.append(
                _record(book, model, attempt, completion.usage, CallOutcome.SUCCEEDED, None)
            )
            usage, cost = _totals(calls)
            return StructuredResult(
                value=value,  # type: ignore[arg-type]
                model=model.spec.name,
                prompt_hash=prompt_hash,
                usage=usage,
                cost_usd=cost,
                attempts=len(calls),
                repairs=repairs,
                calls=tuple(calls),
            )

        rejected_text = completion.text
        repairable = repairs < max_repairs
        calls.append(
            _record(
                book,
                model,
                attempt,
                completion.usage,
                CallOutcome.REPAIRED if repairable else CallOutcome.FAILED,
                rejection.code,
            )
        )
        if not repairable:
            raise StructuredOutputInvalid(
                f"{schema.__name__} was still invalid after {repairs} repairs: "
                f"{rejection.instruction}",
                raw_text=rejected_text,
                repairs=repairs,
            )
        repairs += 1
        request = replace(
            request,
            messages=prompt
            + (
                assistant_message(_echo(rejected_text)),
                user_message(rejection.instruction),
            ),
        )


def _validate[T: BaseModel](
    completion: Completion,
    schema: type[T],
) -> tuple[T | None, _Rejection | None]:
    if completion.is_empty:
        return None, _Rejection(
            RejectionCode.EMPTY_RESPONSE,
            "Your previous reply was empty. Reply again with the complete JSON object "
            "described above, and nothing else.",
        )
    payload, error = _load_json(completion.text)
    if error is not None:
        return None, _Rejection(
            RejectionCode.INVALID_JSON,
            f"Your previous reply was not valid JSON ({error}). Reply with one JSON object, "
            "starting with '{' and ending with '}', and no other text.",
        )
    try:
        return schema.model_validate(payload), None
    except ValidationError as invalid:
        return None, _Rejection(
            RejectionCode.SCHEMA_VIOLATION,
            "Your previous reply did not match the schema. Fix exactly these problems and "
            f"reply with the corrected JSON object:\n{_format_errors(invalid)}",
        )


def _load_json(text: str) -> tuple[Any, str | None]:
    """Parse a reply that should be one JSON object, tolerating a markdown fence.

    Models fence JSON out of habit even when told not to, and stripping the fence is
    cheaper than a repair round trip. Nothing else is repaired here: guessing at broken
    JSON is how a hallucinated field ends up in a tutorial.
    """
    candidate = _strip_fence(text.strip())
    try:
        return json.loads(candidate), None
    except json.JSONDecodeError as error:
        start, end = candidate.find("{"), candidate.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(candidate[start : end + 1]), None
            except json.JSONDecodeError:
                return None, error.msg
        return None, error.msg


def _strip_fence(text: str) -> str:
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[1] if "\n" in text else ""
    fence = body.rfind("```")
    return (body[:fence] if fence >= 0 else body).strip()


def _format_errors(invalid: ValidationError) -> str:
    lines = [
        f"- {'.'.join(str(part) for part in error['loc']) or '(root)'}: {error['msg']}"
        for error in invalid.errors()[:_MAX_REPORTED_ERRORS]
    ]
    return "\n".join(lines)


def _echo(text: str) -> str:
    return text if len(text) <= RAW_TEXT_ECHO_LIMIT else f"{text[:RAW_TEXT_ECHO_LIMIT]}..."


def _record(
    ledger: UsageLedger,
    model: ChatModel,
    attempt: int,
    usage: TokenUsage,
    outcome: CallOutcome,
    error_code: str | None,
) -> ModelCall:
    return ledger.record(
        model=model.spec.name,
        attempt=attempt,
        usage=usage,
        price=model.spec.price,
        outcome=outcome,
        error_code=error_code,
    )


def _totals(calls: Sequence[ModelCall]) -> tuple[TokenUsage, Decimal]:
    usage = TokenUsage()
    cost = Decimal("0")
    for call in calls:
        usage = usage + call.usage
        cost += call.cost_usd
    return usage, cost


__all__ = [
    "DEFAULT_MAX_REPAIRS",
    "DEFAULT_RETRY_POLICY",
    "RAW_TEXT_ECHO_LIMIT",
    "RejectionCode",
    "RetryPolicy",
    "StructuredResult",
    "generate_structured",
    "json_contract",
]
