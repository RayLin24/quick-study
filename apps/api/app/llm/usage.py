"""Token counts and what they cost.

Every model call is recorded, including the ones that failed or had to be repaired: the
provider bills for those too, and a run that only counted its successes would report a
cost the operator's invoice disagrees with.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from app.llm.errors import CostBudgetExceeded

if TYPE_CHECKING:
    from app.llm.pricing import PriceSnapshot


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What one or more calls consumed.

    ``cached_input_tokens`` is the part of ``input_tokens`` that hit the provider's prompt
    cache. It is a subset rather than a separate bucket because that is how the provider
    reports it, and mixing the two up would misprice a cached prompt by two orders of
    magnitude.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        if min(self.input_tokens, self.output_tokens, self.cached_input_tokens) < 0:
            raise ValueError("token counts cannot be negative")
        if self.reasoning_tokens < 0:
            raise ValueError("token counts cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens are a subset of the input tokens")

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    @property
    def billable_input_tokens(self) -> int:
        """Input tokens charged at the full rate, i.e. the ones that missed the cache."""
        return self.input_tokens - self.cached_input_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, Any] | None) -> TokenUsage:
        """Read LangChain's ``usage_metadata``, tolerating a provider that omits it.

        A missing count means "not reported", not zero cost, but a step must not fail
        because a provider left usage out of a response.
        """
        if not metadata:
            return cls()
        input_tokens = _count(metadata, "input_tokens")
        cached = _count(metadata.get("input_token_details"), "cache_read")
        return cls(
            input_tokens=input_tokens,
            output_tokens=_count(metadata, "output_tokens"),
            cached_input_tokens=min(cached, input_tokens),
            reasoning_tokens=_count(metadata.get("output_token_details"), "reasoning"),
        )


class CallOutcome(StrEnum):
    """What happened to one round trip."""

    SUCCEEDED = "succeeded"
    #: The call failed transiently and another attempt followed.
    RETRIED = "retried"
    #: The call returned something unusable and a repair prompt followed.
    REPAIRED = "repaired"
    #: The call was the last one; the generation gave up here.
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ModelCall:
    """One round trip to a provider, successful or not."""

    model: str
    attempt: int
    usage: TokenUsage
    cost_usd: Decimal
    outcome: CallOutcome
    error_code: str | None = None


class UsageLedger:
    """Accumulates model calls for a run, optionally against a spend ceiling.

    The ledger is the single place a step, a run and the UI read cost from, so a caller
    can share one across every generation in a node and hand the totals straight to
    :func:`app.runs.steps.complete_step`.
    """

    def __init__(self, *, budget_usd: Decimal | None = None) -> None:
        self._calls: list[ModelCall] = []
        self._budget_usd = budget_usd

    @property
    def calls(self) -> tuple[ModelCall, ...]:
        return tuple(self._calls)

    @property
    def usage(self) -> TokenUsage:
        total = TokenUsage()
        for call in self._calls:
            total = total + call.usage
        return total

    @property
    def cost_usd(self) -> Decimal:
        return sum((call.cost_usd for call in self._calls), Decimal("0"))

    def record(
        self,
        *,
        model: str,
        attempt: int,
        usage: TokenUsage,
        price: PriceSnapshot,
        outcome: CallOutcome,
        error_code: str | None = None,
    ) -> ModelCall:
        """Record one call, then refuse to continue if the run is now over budget.

        The call is kept before the budget is enforced: the tokens were already spent, and
        an operator investigating the stop needs to see what spent them.
        """
        call = ModelCall(
            model=model,
            attempt=attempt,
            usage=usage,
            cost_usd=price.cost_for(usage),
            outcome=outcome,
            error_code=error_code,
        )
        self._calls.append(call)
        if self._budget_usd is not None and self.cost_usd > self._budget_usd:
            raise CostBudgetExceeded(
                f"spent {self.cost_usd} USD against a {self._budget_usd} USD budget"
            )
        return call

    def step_fields(self) -> dict[str, Any]:
        """Return the accounting columns of ``steps``, ready to pass to ``complete_step``."""
        usage = self.usage
        return {
            "model": self._calls[-1].model if self._calls else None,
            "tokens_in": usage.input_tokens,
            "tokens_out": usage.output_tokens,
            "cost_usd": self.cost_usd,
        }


def _count(source: Any, key: str) -> int:
    value = source.get(key) if isinstance(source, Mapping) else None
    return value if isinstance(value, int) and value > 0 else 0
