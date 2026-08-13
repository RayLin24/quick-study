"""Error classification for model calls.

The split that matters operationally is retry versus block. Rate limits and server faults
are worth waiting out; a rejected request, a bad key or an empty balance will fail exactly
the same way on every attempt, so retrying them only burns the run's time budget and,
for 402, hides the one thing an operator has to act on.
"""

from __future__ import annotations

import pytest
from llm_support import FakeProviderError

from app.llm.errors import (
    BlockingModelError,
    ModelAuthenticationError,
    ModelConnectionError,
    ModelError,
    ModelInvalidRequest,
    ModelQuotaExhausted,
    ModelRateLimited,
    ModelServerError,
    ModelTimeout,
    ModelUnsupportedCapability,
    RetryableModelError,
    StructuredOutputInvalid,
    classify_provider_error,
    error_for_status,
    is_retryable,
)


class TestStatusClassification:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (408, ModelTimeout),
            (429, ModelRateLimited),
            (500, ModelServerError),
            (502, ModelServerError),
            (503, ModelServerError),
            (504, ModelServerError),
        ],
    )
    def test_transient_statuses_are_retryable(
        self, status: int, expected: type[ModelError]
    ) -> None:
        error = error_for_status(status, "upstream said no")

        assert isinstance(error, expected)
        assert isinstance(error, RetryableModelError)
        assert is_retryable(error)

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (400, ModelInvalidRequest),
            (401, ModelAuthenticationError),
            (402, ModelQuotaExhausted),
            (403, ModelAuthenticationError),
            (404, ModelInvalidRequest),
            (422, ModelInvalidRequest),
        ],
    )
    def test_request_faults_block_immediately(
        self,
        status: int,
        expected: type[ModelError],
    ) -> None:
        error = error_for_status(status, "upstream said no")

        assert isinstance(error, expected)
        assert isinstance(error, BlockingModelError)
        assert not is_retryable(error)

    def test_an_unknown_status_blocks_rather_than_hammering_the_provider(self) -> None:
        assert isinstance(error_for_status(418, "teapot"), BlockingModelError)

    def test_the_status_and_message_stay_on_the_error(self) -> None:
        error = error_for_status(429, "too many requests")

        assert error.status_code == 429
        assert "too many requests" in str(error)

    def test_a_rate_limit_keeps_the_retry_after_hint(self) -> None:
        error = error_for_status(429, "slow down", retry_after=12.5)

        assert isinstance(error, ModelRateLimited)
        assert error.retry_after == 12.5


class TestProviderExceptionClassification:
    def test_a_status_carrying_exception_is_mapped_by_status(self) -> None:
        classified = classify_provider_error(FakeProviderError("rate limited", 429))

        assert isinstance(classified, ModelRateLimited)

    def test_a_status_nested_on_a_response_object_is_still_found(self) -> None:
        class Response:
            status_code = 503

        class Failure(Exception):
            response = Response()

        assert isinstance(classify_provider_error(Failure("overloaded")), ModelServerError)

    def test_a_timeout_without_a_status_is_retryable(self) -> None:
        class APITimeoutError(Exception):
            pass

        assert isinstance(classify_provider_error(APITimeoutError("timed out")), ModelTimeout)

    def test_a_connection_failure_without_a_status_is_retryable(self) -> None:
        class APIConnectionError(Exception):
            pass

        assert isinstance(
            classify_provider_error(APIConnectionError("connection reset")),
            ModelConnectionError,
        )

    def test_an_unrecognised_failure_blocks_and_names_its_type(self) -> None:
        classified = classify_provider_error(ValueError("something structural"))

        assert isinstance(classified, BlockingModelError)
        assert "ValueError" in str(classified)

    def test_an_already_classified_error_passes_through_unchanged(self) -> None:
        original = error_for_status(429, "slow down")

        assert classify_provider_error(original) is original

    def test_the_api_key_never_survives_into_the_error_message(self) -> None:
        """SDK errors quote the failing request, which can carry the Authorization header."""
        leaky = FakeProviderError("401 for key sk-secret-value on /chat/completions", 401)

        classified = classify_provider_error(leaky, secret="sk-secret-value")

        assert "sk-secret-value" not in str(classified)
        assert "/chat/completions" in str(classified)


class TestGenerationErrors:
    def test_output_that_never_validates_blocks_the_step(self) -> None:
        error = StructuredOutputInvalid("field required", raw_text="{}", repairs=2)

        assert isinstance(error, BlockingModelError)
        assert error.raw_text == "{}"
        assert error.repairs == 2

    def test_asking_a_model_for_something_it_cannot_do_blocks(self) -> None:
        assert isinstance(
            ModelUnsupportedCapability("deepseek-reasoner cannot return JSON"),
            BlockingModelError,
        )
