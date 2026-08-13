"""Structured generation: validation, bounded repair, retries and accounting.

DeepSeek's JSON mode occasionally returns empty content, and any model can emit JSON that
does not fit the schema. Both are recoverable by re-prompting, so the generator repairs a
bounded number of times and then gives up loudly rather than letting an unvalidated
payload reach the tutorial document. Transport faults are a different failure: they are
retried with backoff, and every attempt is paid for whether or not it produced anything.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from llm_support import (
    STRUCTURED_SPEC,
    TEXT_ONLY_SPEC,
    FakeChatModel,
    RecordingSleeper,
    reply,
)
from pydantic import BaseModel, Field

from app.llm.errors import (
    ModelAuthenticationError,
    ModelInvalidRequest,
    ModelQuotaExhausted,
    ModelRateLimited,
    ModelServerError,
    ModelUnsupportedCapability,
    StructuredOutputInvalid,
    error_for_status,
)
from app.llm.providers import MessageRole, user_message
from app.llm.structured import RejectionCode, RetryPolicy, generate_structured
from app.llm.usage import CallOutcome, TokenUsage, UsageLedger


class Answer(BaseModel):
    """The smallest schema that can be got wrong in every interesting way."""

    title: str
    steps: list[str] = Field(min_length=1)


VALID = '{"title": "Deploy the gateway", "steps": ["install", "configure"]}'
MESSAGES = (user_message("Summarise the deployment guide."),)
NO_WAIT = RetryPolicy(max_attempts=3, initial_backoff=0.5, multiplier=2.0, max_backoff=8.0)


def generate(model: FakeChatModel, **overrides: object) -> object:
    kwargs: dict[str, object] = {
        "schema": Answer,
        "messages": MESSAGES,
        "policy": NO_WAIT,
        "sleep": RecordingSleeper(),
    }
    kwargs.update(overrides)
    return generate_structured(model, **kwargs)  # type: ignore[arg-type]


class TestHappyPath:
    def test_valid_output_is_parsed_into_the_schema(self) -> None:
        result = generate(FakeChatModel([reply(VALID)]))

        assert result.value == Answer(title="Deploy the gateway", steps=["install", "configure"])
        assert result.attempts == 1
        assert result.repairs == 0

    def test_the_request_carries_the_schema_so_the_provider_can_constrain_output(self) -> None:
        model = FakeChatModel([reply(VALID)])

        generate(model)

        request = model.requests[0]
        assert request.response_schema == Answer.model_json_schema()
        assert request.schema_name == "Answer"

    def test_the_prompt_spells_out_json_because_deepseek_requires_the_word(self) -> None:
        """DeepSeek's JSON mode rejects a request whose prompt never mentions JSON."""
        model = FakeChatModel([reply(VALID)])

        generate(model)

        assert "json" in model.requests[0].messages[-1].content.lower()

    def test_the_caller_messages_are_sent_untouched_and_first(self) -> None:
        model = FakeChatModel([reply(VALID)])

        generate(model)

        assert model.requests[0].messages[0] == MESSAGES[0]

    def test_the_prompt_hash_identifies_the_original_prompt_for_provenance(self) -> None:
        model = FakeChatModel([reply(VALID)])

        result = generate(model)

        assert result.prompt_hash == model.requests[0].prompt_hash()
        assert len(result.prompt_hash) == 64

    def test_usage_and_cost_are_reported_for_the_call(self) -> None:
        model = FakeChatModel([reply(VALID, usage=TokenUsage(input_tokens=1_000_000))])

        result = generate(model)

        assert result.usage.input_tokens == 1_000_000
        assert result.cost_usd == Decimal("1.000000")
        assert result.model == STRUCTURED_SPEC.name


class TestBoundedRepair:
    def test_an_empty_response_is_repaired_by_reprompting(self) -> None:
        model = FakeChatModel([reply(""), reply(VALID)])

        result = generate(model)

        assert result.value.title == "Deploy the gateway"
        assert result.repairs == 1
        assert result.attempts == 2

    def test_the_repair_prompt_says_the_previous_reply_was_empty(self) -> None:
        model = FakeChatModel([reply("   "), reply(VALID)])

        generate(model)

        assert "empty" in model.requests[1].messages[-1].content.lower()

    def test_the_ledger_names_why_a_call_had_to_be_repaired(self) -> None:
        ledger = UsageLedger()

        generate(FakeChatModel([reply(""), reply(VALID)]), ledger=ledger)

        assert ledger.calls[0].error_code == RejectionCode.EMPTY_RESPONSE

    def test_unparseable_json_is_repaired_with_the_parse_diagnosis(self) -> None:
        model = FakeChatModel([reply("Sure! here is the json: {title:"), reply(VALID)])

        result = generate(model)

        assert result.repairs == 1
        repair_prompt = model.requests[1].messages[-1].content
        assert "json" in repair_prompt.lower()

    def test_output_that_breaks_the_schema_is_repaired_with_the_validation_errors(self) -> None:
        model = FakeChatModel([reply('{"title": "Deploy", "steps": []}'), reply(VALID)])

        generate(model)

        repair_prompt = model.requests[1].messages[-1].content
        assert "steps" in repair_prompt

    def test_the_rejected_reply_is_echoed_back_as_the_assistant_turn(self) -> None:
        model = FakeChatModel([reply('{"title": 12}'), reply(VALID)])

        generate(model)

        echoed = model.requests[1].messages[-2]
        assert echoed.role is MessageRole.ASSISTANT
        assert '{"title": 12}' in echoed.content

    def test_repairs_are_bounded_and_then_the_step_fails(self) -> None:
        model = FakeChatModel([reply("{}"), reply("{}"), reply("{}")])

        with pytest.raises(StructuredOutputInvalid) as failure:
            generate(model, max_repairs=2)

        assert model.calls == 3
        assert failure.value.repairs == 2
        assert failure.value.raw_text == "{}"

    def test_every_repair_attempt_is_still_charged_to_the_run(self) -> None:
        ledger = UsageLedger()
        model = FakeChatModel([reply("{}"), reply("{}"), reply("{}")])

        with pytest.raises(StructuredOutputInvalid):
            generate(model, max_repairs=2, ledger=ledger)

        assert len(ledger.calls) == 3
        assert ledger.usage.input_tokens == 300
        assert ledger.calls[0].outcome is CallOutcome.REPAIRED
        assert ledger.calls[-1].outcome is CallOutcome.FAILED

    def test_a_reply_wrapped_in_a_markdown_fence_is_still_accepted(self) -> None:
        """Models fence JSON out of habit; that is not worth a repair round trip."""
        model = FakeChatModel([reply(f"```json\n{VALID}\n```")])

        result = generate(model)

        assert result.value.title == "Deploy the gateway"
        assert result.repairs == 0


class TestRetryAndErrorClassification:
    def test_a_rate_limit_is_retried_after_a_backoff(self) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel([error_for_status(429, "slow down"), reply(VALID)])

        result = generate(model, sleep=sleeper)

        assert result.attempts == 2
        assert sleeper.delays == [0.5]

    def test_server_faults_back_off_exponentially(self) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel(
            [error_for_status(503, "overloaded"), error_for_status(500, "boom"), reply(VALID)]
        )

        generate(model, sleep=sleeper, policy=RetryPolicy(max_attempts=3))

        assert sleeper.delays == [0.5, 1.0]

    def test_a_retry_after_hint_wins_over_the_computed_backoff(self) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel([error_for_status(429, "slow down", retry_after=5.0), reply(VALID)])

        generate(model, sleep=sleeper)

        assert sleeper.delays == [5.0]

    def test_a_retry_after_hint_is_still_capped_so_a_lease_cannot_expire(self) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel([error_for_status(429, "slow down", retry_after=600.0), reply(VALID)])

        generate(model, sleep=sleeper)

        assert sleeper.delays == [NO_WAIT.max_backoff]

    def test_retries_run_out_and_the_last_transport_error_surfaces(self) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel([error_for_status(503, "a"), error_for_status(503, "b")])

        with pytest.raises(ModelServerError):
            generate(model, sleep=sleeper, policy=RetryPolicy(max_attempts=2))

        assert model.calls == 2
        assert sleeper.delays == [0.5]

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (400, ModelInvalidRequest),
            (401, ModelAuthenticationError),
            (402, ModelQuotaExhausted),
            (422, ModelInvalidRequest),
        ],
    )
    def test_a_blocking_failure_is_never_retried(self, status: int, expected: type) -> None:
        sleeper = RecordingSleeper()
        model = FakeChatModel([error_for_status(status, "refused"), reply(VALID)])

        with pytest.raises(expected):
            generate(model, sleep=sleeper)

        assert model.calls == 1
        assert sleeper.delays == []

    def test_a_raw_provider_exception_is_classified_before_the_retry_decision(self) -> None:
        from llm_support import FakeProviderError

        sleeper = RecordingSleeper()
        model = FakeChatModel([FakeProviderError("429 too many", 429), reply(VALID)])

        result = generate(model, sleep=sleeper)

        assert result.attempts == 2
        assert sleeper.delays == [0.5]

    def test_a_retried_call_is_recorded_with_its_error_code(self) -> None:
        ledger = UsageLedger()
        model = FakeChatModel([error_for_status(429, "slow down"), reply(VALID)])

        generate(model, ledger=ledger)

        assert [call.outcome for call in ledger.calls] == [
            CallOutcome.RETRIED,
            CallOutcome.SUCCEEDED,
        ]
        assert ledger.calls[0].error_code == ModelRateLimited.code


class TestCapabilityGuard:
    def test_a_model_that_cannot_return_json_is_refused_before_any_call(self) -> None:
        """``deepseek-reasoner`` supports neither tool calling nor structured output."""
        model = FakeChatModel([reply(VALID)], spec=TEXT_ONLY_SPEC)

        with pytest.raises(ModelUnsupportedCapability):
            generate(model)

        assert model.calls == 0


class TestSharedLedger:
    def test_two_generations_accumulate_in_one_ledger_but_report_separately(self) -> None:
        ledger = UsageLedger()
        first = generate(FakeChatModel([reply(VALID)]), ledger=ledger)
        second = generate(FakeChatModel([reply(VALID)]), ledger=ledger)

        assert len(ledger.calls) == 2
        assert first.usage.input_tokens == 100
        assert second.usage.input_tokens == 100
        assert ledger.usage.input_tokens == 200
