"""The graph state and its reducers.

The reducers are where the workflow's invariants live: a phase never moves backwards
except for a rejected outline, a locked chapter survives a partial regeneration, and node
telemetry accumulates instead of being overwritten. They are pure functions so these
tests need neither a database nor a graph.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.db.models.enums import ChapterStatus, RunPhase
from app.runs.state_machine import IllegalRunPhase
from app.workflows.tutorial.state import (
    PIPELINE_VERSION,
    accumulate_usage,
    advance_phase_value,
    append_records,
    chapter_draft,
    input_digest,
    merge_attempts,
    merge_chapters,
    new_tutorial_state,
    node_record,
    tutorial_request,
)


class TestPhaseReducer:
    def test_moves_forward_through_the_pipeline(self) -> None:
        assert advance_phase_value(RunPhase.OUTLINE.value, RunPhase.HUMAN_INTERRUPT.value) == (
            RunPhase.HUMAN_INTERRUPT.value
        )

    def test_refuses_to_move_backwards(self) -> None:
        with pytest.raises(IllegalRunPhase):
            advance_phase_value(RunPhase.CHAPTERS.value, RunPhase.PARSE.value)

    def test_allows_the_one_rejection_path_back_to_outline(self) -> None:
        assert advance_phase_value(RunPhase.HUMAN_INTERRUPT.value, RunPhase.OUTLINE.value) == (
            RunPhase.OUTLINE.value
        )

    def test_re_entering_the_same_phase_is_a_retry_not_a_regression(self) -> None:
        assert advance_phase_value(RunPhase.PARSE.value, RunPhase.PARSE.value) == (
            RunPhase.PARSE.value
        )

    def test_an_absent_incoming_phase_keeps_the_current_one(self) -> None:
        assert advance_phase_value(RunPhase.INDEX.value, None) == RunPhase.INDEX.value


class TestChapterReducer:
    def test_adds_new_chapters(self) -> None:
        incoming = {"intro": chapter_draft(slug="intro", title="Intro", ordinal=0)}
        assert merge_chapters({}, incoming)["intro"]["title"] == "Intro"

    def test_a_later_revision_replaces_an_unlocked_chapter(self) -> None:
        current = {"intro": chapter_draft(slug="intro", title="Intro", ordinal=0)}
        incoming = {
            "intro": chapter_draft(slug="intro", title="Introduction", ordinal=0, revision=2)
        }
        assert merge_chapters(current, incoming)["intro"]["title"] == "Introduction"

    def test_a_locked_chapter_is_never_overwritten_by_a_regeneration(self) -> None:
        current = {
            "intro": chapter_draft(
                slug="intro",
                title="Reviewed introduction",
                ordinal=0,
                status=ChapterStatus.LOCKED,
                locked=True,
                revision=3,
            )
        }
        incoming = {
            "intro": chapter_draft(slug="intro", title="Regenerated", ordinal=0, revision=4)
        }
        merged = merge_chapters(current, incoming)
        assert merged["intro"]["title"] == "Reviewed introduction"
        assert merged["intro"]["revision"] == 3

    def test_locking_a_chapter_is_itself_applied(self) -> None:
        current = {"intro": chapter_draft(slug="intro", title="Intro", ordinal=0)}
        incoming = {
            "intro": chapter_draft(
                slug="intro",
                title="Intro",
                ordinal=0,
                status=ChapterStatus.LOCKED,
                locked=True,
                revision=2,
            )
        }
        assert merge_chapters(current, incoming)["intro"]["locked"] is True

    def test_a_locked_chapter_does_not_block_its_siblings(self) -> None:
        current = {
            "intro": chapter_draft(slug="intro", title="Kept", ordinal=0, locked=True),
            "setup": chapter_draft(slug="setup", title="Old setup", ordinal=1),
        }
        incoming = {
            "intro": chapter_draft(slug="intro", title="Lost", ordinal=0),
            "setup": chapter_draft(slug="setup", title="New setup", ordinal=1, revision=2),
        }
        merged = merge_chapters(current, incoming)
        assert merged["intro"]["title"] == "Kept"
        assert merged["setup"]["title"] == "New setup"


class TestTelemetryReducers:
    def test_records_accumulate_in_order(self) -> None:
        first = node_record(node="outline", phase=RunPhase.OUTLINE, input_hash="a" * 64, attempt=1)
        second = node_record(node="outline", phase=RunPhase.OUTLINE, input_hash="a" * 64, attempt=2)
        assert [r["attempt"] for r in append_records([first], [second])] == [1, 2]

    def test_a_record_carries_the_full_provenance_of_the_node(self) -> None:
        record = node_record(
            node="chapters",
            phase=RunPhase.CHAPTERS,
            input_hash="b" * 64,
            attempt=2,
            prompt_hash="c" * 64,
            model="deepseek-chat",
            tokens_in=120,
            tokens_out=340,
            cost_usd=Decimal("0.0042"),
            error_code="timeout",
            error_message="the model took too long",
            idempotency_key="run:chapters:chapters:0",
        )
        assert record["pipeline_version"] == PIPELINE_VERSION
        assert record["input_hash"] == "b" * 64
        assert record["prompt_hash"] == "c" * 64
        assert record["model"] == "deepseek-chat"
        assert record["attempt"] == 2
        assert record["tokens_in"] == 120
        assert record["tokens_out"] == 340
        assert record["cost_usd"] == "0.0042"
        assert record["error_code"] == "timeout"
        assert record["error_message"] == "the model took too long"
        assert record["idempotency_key"] == "run:chapters:chapters:0"

    def test_usage_totals_add_up(self) -> None:
        total = accumulate_usage(
            {"tokens_in": 10, "tokens_out": 20, "cost_usd": "0.10"},
            {"tokens_in": 5, "tokens_out": 1, "cost_usd": "0.05"},
        )
        assert total == {"tokens_in": 15, "tokens_out": 21, "cost_usd": "0.15"}

    def test_attempts_keep_the_highest_count_per_node(self) -> None:
        assert merge_attempts({"parse": 2, "index": 1}, {"parse": 1, "index": 3}) == {
            "parse": 2,
            "index": 3,
        }


class TestInputDigest:
    def test_is_stable_across_key_order(self) -> None:
        assert input_digest({"a": 1, "b": [2, 3]}) == input_digest({"b": [2, 3], "a": 1})

    def test_changes_when_the_payload_changes(self) -> None:
        assert input_digest({"a": 1}) != input_digest({"a": 2})

    def test_is_a_sha256_hex_digest(self) -> None:
        digest = input_digest({"a": 1})
        assert len(digest) == 64
        assert digest == digest.lower()


class TestInitialState:
    def test_starts_queued_with_the_request_attached(self) -> None:
        request = tutorial_request(
            title="Gateway tutorial",
            reader_level="beginner",
            length_preset="standard",
            languages=["python"],
            sources=[{"kind": "website", "locator": "https://docs.example.test/"}],
        )
        state = new_tutorial_state(
            run_id="run-1",
            project_id="project-1",
            thread_id="thread-1",
            request=request,
        )
        assert state["phase"] == RunPhase.QUEUED.value
        assert state["pipeline_version"] == PIPELINE_VERSION
        assert state["request"]["title"] == "Gateway tutorial"
        assert state["chapters"] == {}
        assert state["records"] == []
        assert state["usage"] == {"tokens_in": 0, "tokens_out": 0, "cost_usd": "0"}

    def test_a_source_reference_is_normalised_with_a_fingerprint(self) -> None:
        request = tutorial_request(
            title="t",
            sources=[{"kind": "github_repo", "locator": "octocat/hello-world"}],
        )
        source = request["sources"][0]
        assert source["kind"] == "github_repo"
        assert len(source["fingerprint"]) == 64
