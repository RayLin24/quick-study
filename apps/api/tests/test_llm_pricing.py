"""Token accounting and cost estimation.

A self-hosted deployment pays per token, so a run has to be able to say what it spent
even when most of its calls failed. Prices are a dated snapshot, not a live lookup: the
provider changes them, and a run's recorded cost must stay reproducible afterwards.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from llm_support import TEST_PRICE

from app.llm.errors import CostBudgetExceeded
from app.llm.pricing import (
    DEEPSEEK_V4_FLASH_PRICE,
    PRICE_BOOK,
    TOKENS_PER_PRICE_UNIT,
    UnknownModelPrice,
    resolve_price,
)
from app.llm.usage import CallOutcome, TokenUsage, UsageLedger

MILLION = int(TOKENS_PER_PRICE_UNIT)


class TestTokenUsage:
    def test_usage_adds_up_field_by_field(self) -> None:
        total = TokenUsage(input_tokens=10, output_tokens=5, cached_input_tokens=4) + TokenUsage(
            input_tokens=2, output_tokens=1, cached_input_tokens=1
        )

        assert total == TokenUsage(input_tokens=12, output_tokens=6, cached_input_tokens=5)

    def test_cached_tokens_are_a_subset_of_the_input_tokens(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=0, cached_input_tokens=4)

        assert usage.billable_input_tokens == 6
        assert usage.total_tokens == 10

    def test_negative_counts_are_refused(self) -> None:
        with pytest.raises(ValueError):
            TokenUsage(input_tokens=-1)

    def test_more_cached_tokens_than_input_tokens_is_refused(self) -> None:
        with pytest.raises(ValueError):
            TokenUsage(input_tokens=3, cached_input_tokens=4)

    def test_usage_is_read_from_the_langchain_metadata_shape(self) -> None:
        usage = TokenUsage.from_metadata(
            {
                "input_tokens": 120,
                "output_tokens": 42,
                "total_tokens": 162,
                "input_token_details": {"cache_read": 100},
                "output_token_details": {"reasoning": 12},
            }
        )

        assert usage == TokenUsage(
            input_tokens=120,
            output_tokens=42,
            cached_input_tokens=100,
            reasoning_tokens=12,
        )

    def test_missing_metadata_counts_as_no_usage_rather_than_an_error(self) -> None:
        """A provider that omits usage must not crash a step; the cost is simply unknown."""
        assert TokenUsage.from_metadata(None) == TokenUsage()


class TestPriceSnapshot:
    def test_a_million_cache_miss_input_tokens_cost_the_published_rate(self) -> None:
        cost = DEEPSEEK_V4_FLASH_PRICE.cost_for(TokenUsage(input_tokens=MILLION))

        assert cost == Decimal("0.140000")

    def test_cached_input_tokens_are_billed_at_the_cache_hit_rate(self) -> None:
        cost = DEEPSEEK_V4_FLASH_PRICE.cost_for(
            TokenUsage(input_tokens=MILLION, cached_input_tokens=MILLION)
        )

        assert cost == Decimal("0.002800")

    def test_output_tokens_use_the_output_rate(self) -> None:
        cost = DEEPSEEK_V4_FLASH_PRICE.cost_for(TokenUsage(output_tokens=MILLION))

        assert cost == Decimal("0.280000")

    def test_the_three_token_buckets_are_summed(self) -> None:
        cost = TEST_PRICE.cost_for(
            TokenUsage(
                input_tokens=MILLION,
                cached_input_tokens=MILLION // 2,
                output_tokens=MILLION,
            )
        )

        assert cost == Decimal("2.550000")

    def test_costs_are_quantised_to_the_precision_the_database_stores(self) -> None:
        cost = DEEPSEEK_V4_FLASH_PRICE.cost_for(TokenUsage(input_tokens=1))

        assert cost == Decimal("0.000000")
        assert cost.as_tuple().exponent == -6

    def test_no_usage_costs_nothing(self) -> None:
        assert TEST_PRICE.cost_for(TokenUsage()) == Decimal("0.000000")

    def test_a_snapshot_records_when_it_was_taken_and_where_from(self) -> None:
        assert DEEPSEEK_V4_FLASH_PRICE.captured_on.year >= 2026
        assert DEEPSEEK_V4_FLASH_PRICE.source.startswith("https://")
        assert DEEPSEEK_V4_FLASH_PRICE.currency == "USD"


class TestPriceBook:
    def test_the_compatibility_aliases_resolve_to_the_model_they_point_at(self) -> None:
        """``deepseek-chat`` and ``deepseek-reasoner`` are modes of the same billed model."""
        assert resolve_price("deepseek-chat") == DEEPSEEK_V4_FLASH_PRICE
        assert resolve_price("deepseek-reasoner") == DEEPSEEK_V4_FLASH_PRICE
        assert resolve_price("deepseek-v4-flash") == DEEPSEEK_V4_FLASH_PRICE

    def test_an_unpriced_model_is_refused_instead_of_costing_nothing(self) -> None:
        with pytest.raises(UnknownModelPrice):
            resolve_price("some-model-nobody-registered")

    def test_every_published_price_is_positive(self) -> None:
        for price in PRICE_BOOK.values():
            assert price.output_per_million > 0
            assert price.input_cache_miss_per_million > 0
            assert price.input_cache_hit_per_million > 0


class TestUsageLedger:
    def test_a_failed_attempt_is_still_paid_for(self) -> None:
        ledger = UsageLedger()
        ledger.record(
            model="fake-model",
            attempt=1,
            usage=TokenUsage(input_tokens=MILLION, output_tokens=0),
            price=TEST_PRICE,
            outcome=CallOutcome.RETRIED,
            error_code="rate_limited",
        )
        ledger.record(
            model="fake-model",
            attempt=2,
            usage=TokenUsage(input_tokens=MILLION, output_tokens=MILLION),
            price=TEST_PRICE,
            outcome=CallOutcome.SUCCEEDED,
        )

        assert ledger.usage.input_tokens == 2 * MILLION
        assert ledger.cost_usd == Decimal("4.000000")
        assert [call.outcome for call in ledger.calls] == [
            CallOutcome.RETRIED,
            CallOutcome.SUCCEEDED,
        ]

    def test_the_ledger_hands_the_step_row_exactly_the_columns_it_stores(self) -> None:
        ledger = UsageLedger()
        ledger.record(
            model="deepseek-chat",
            attempt=1,
            usage=TokenUsage(input_tokens=1_000, output_tokens=500),
            price=DEEPSEEK_V4_FLASH_PRICE,
            outcome=CallOutcome.SUCCEEDED,
        )

        assert ledger.step_fields() == {
            "model": "deepseek-chat",
            "tokens_in": 1_000,
            "tokens_out": 500,
            "cost_usd": Decimal("0.000280"),
        }

    def test_a_budget_stops_the_run_and_keeps_the_call_that_broke_it(self) -> None:
        ledger = UsageLedger(budget_usd=Decimal("1.00"))

        with pytest.raises(CostBudgetExceeded):
            ledger.record(
                model="fake-model",
                attempt=1,
                usage=TokenUsage(input_tokens=2 * MILLION),
                price=TEST_PRICE,
                outcome=CallOutcome.SUCCEEDED,
            )

        assert ledger.cost_usd == Decimal("2.000000")

    def test_an_empty_ledger_reports_no_spend(self) -> None:
        ledger = UsageLedger()

        assert ledger.cost_usd == Decimal("0")
        assert ledger.usage == TokenUsage()
        assert ledger.step_fields()["model"] is None
