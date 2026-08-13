"""The tutorial generation graph.

These tests run the whole graph with the in-memory checkpointer and stub nodes: no model
is called, nothing is fetched and no MySQL server is needed. What they pin down is the
shape of the pipeline, the outline approval interrupt, the telemetry every node has to
record and the guarantees around retries and locked chapters.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from langgraph.types import Command

from app.db.models.enums import ApprovalDecision, ChapterStatus, RunPhase
from app.runs.steps import ClaimOutcome
from app.workflows.checkpointing import InMemoryCheckpointerProvider
from app.workflows.tutorial import (
    GRAPH_NODES,
    NODE_PHASES,
    NodeOutcome,
    TutorialNodes,
    compile_tutorial_graph,
)
from app.workflows.tutorial.recording import InMemoryStepRecorder
from app.workflows.tutorial.state import (
    PIPELINE_VERSION,
    chapter_draft,
    new_tutorial_state,
    tutorial_request,
)

APPROVE = {"decision": ApprovalDecision.APPROVED.value, "note": "looks good"}
REJECT = {"decision": ApprovalDecision.REJECTED.value, "note": "too shallow"}


@pytest.fixture
def recorder() -> InMemoryStepRecorder:
    return InMemoryStepRecorder()


@pytest.fixture
def provider() -> InMemoryCheckpointerProvider:
    provider = InMemoryCheckpointerProvider()
    provider.ensure_schema()
    return provider


def initial_state() -> dict[str, Any]:
    return new_tutorial_state(
        run_id="run-1",
        project_id="project-1",
        thread_id="thread-1",
        request=tutorial_request(
            title="Gateway tutorial",
            reader_level="beginner",
            length_preset="standard",
            languages=["python"],
            sources=[{"kind": "website", "locator": "https://docs.example.test/"}],
        ),
    )


def build(
    provider: InMemoryCheckpointerProvider,
    recorder: InMemoryStepRecorder,
    nodes: TutorialNodes | None = None,
):
    with provider.checkpointer() as checkpointer:
        return compile_tutorial_graph(
            checkpointer=checkpointer,
            nodes=nodes or TutorialNodes(),
            recorder=recorder,
        )


def config(thread_id: str = "thread-1") -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}}


class TestTopology:
    def test_covers_every_documented_phase_in_order(self) -> None:
        assert GRAPH_NODES == (
            "discover",
            "snapshot",
            "parse",
            "index",
            "analyze",
            "outline",
            "human_interrupt",
            "chapters",
            "diagrams",
            "validate",
            "publish",
        )

    def test_every_node_maps_to_the_run_phase_of_the_same_name(self) -> None:
        assert NODE_PHASES == {name: RunPhase(name) for name in GRAPH_NODES}

    def test_the_compiled_graph_exposes_exactly_those_nodes(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        assert set(graph.get_graph().nodes) - {"__start__", "__end__"} == set(GRAPH_NODES)


class TestOutlineApproval:
    def test_the_run_stops_at_the_outline_and_asks_a_human(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        result = graph.invoke(initial_state(), config(), durability="sync")

        interrupts = result["__interrupt__"]
        assert len(interrupts) == 1
        payload = interrupts[0].value
        assert payload["kind"] == "outline_approval"
        assert payload["run_id"] == "run-1"
        assert payload["outline"]["chapters"], "the reviewer needs the proposed chapters"

    def test_nothing_downstream_of_the_interrupt_has_run(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        assert recorder.completed_nodes() == [
            "discover",
            "snapshot",
            "parse",
            "index",
            "analyze",
            "outline",
        ]

    def test_approval_resumes_the_same_thread_and_publishes(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        final = graph.invoke(Command(resume=APPROVE), config(), durability="sync")

        assert final["phase"] == RunPhase.PUBLISH.value
        assert final["approval"]["decision"] == ApprovalDecision.APPROVED.value
        assert final["publication"]["bundle_hash"]
        assert recorder.completed_nodes()[-4:] == ["chapters", "diagrams", "validate", "publish"]

    def test_rejection_is_the_only_step_back_and_regenerates_the_outline(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        second = graph.invoke(Command(resume=REJECT), config(), durability="sync")

        assert second["__interrupt__"], "a rejected outline is proposed again for approval"
        assert second["outline"]["version"] == 2
        assert recorder.completed_nodes().count("outline") == 2
        assert "chapters" not in recorder.completed_nodes()

        final = graph.invoke(Command(resume=APPROVE), config(), durability="sync")
        assert final["phase"] == RunPhase.PUBLISH.value

    def test_a_reviewer_edited_outline_is_what_gets_written(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        edited = {
            **APPROVE,
            "outline": {
                "title": "Reviewer title",
                "summary": "trimmed",
                "chapters": [{"slug": "only", "title": "Only chapter", "ordinal": 0}],
            },
        }
        final = graph.invoke(Command(resume=edited), config(), durability="sync")
        assert final["outline"]["title"] == "Reviewer title"
        assert list(final["chapters"]) == ["only"]


class TestTelemetry:
    def test_every_node_records_its_provenance(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        final = graph.invoke(Command(resume=APPROVE), config(), durability="sync")

        recorded = {record["node"]: record for record in final["records"]}
        assert set(recorded) == set(GRAPH_NODES)
        for name, record in recorded.items():
            assert record["pipeline_version"] == PIPELINE_VERSION
            assert len(record["input_hash"]) == 64
            assert record["attempt"] >= 1
            assert record["phase"] == NODE_PHASES[name].value
            assert record["idempotency_key"].startswith("run-1:")

    def test_a_model_backed_node_reports_its_model_prompt_and_cost(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        def outline(state: dict[str, Any], call: Any) -> NodeOutcome:
            return NodeOutcome(
                update={
                    "outline": {
                        "version": 1,
                        "title": "Generated",
                        "summary": "",
                        "chapters": [{"slug": "a", "title": "A", "ordinal": 0, "summary": ""}],
                    }
                },
                model="deepseek-chat",
                prompt_hash="d" * 64,
                tokens_in=11,
                tokens_out=22,
                cost_usd=Decimal("0.0033"),
            )

        graph = build(provider, recorder, TutorialNodes(outline=outline))
        result = graph.invoke(initial_state(), config(), durability="sync")

        record = next(r for r in result["records"] if r["node"] == "outline")
        assert record["model"] == "deepseek-chat"
        assert record["prompt_hash"] == "d" * 64
        assert record["tokens_in"] == 11
        assert record["cost_usd"] == "0.0033"
        assert result["usage"] == {"tokens_in": 11, "tokens_out": 22, "cost_usd": "0.0033"}

    def test_a_failing_node_records_the_error_before_the_run_fails(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        def parse(state: dict[str, Any], call: Any) -> NodeOutcome:
            raise RuntimeError("the parser fell over")

        graph = build(provider, recorder, TutorialNodes(parse=parse))
        with pytest.raises(RuntimeError, match="the parser fell over"):
            graph.invoke(initial_state(), config(), durability="sync")

        failure = recorder.failures()[-1]
        assert failure.node == "parse"
        assert failure.error_code == "RuntimeError"
        assert "the parser fell over" in failure.error_message

    def test_the_input_hash_of_a_node_is_stable_across_reruns(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config("t1"), durability="sync")
        graph.invoke(initial_state(), config("t2"), durability="sync")
        digests = {call.input_hash for call in recorder.calls if call.node == "discover"}
        assert len(digests) == 1


class TestIdempotency:
    def test_a_node_whose_step_already_succeeded_is_not_executed_again(
        self, provider: InMemoryCheckpointerProvider
    ) -> None:
        executions: list[str] = []

        def analyze(state: dict[str, Any], call: Any) -> NodeOutcome:
            executions.append(call.idempotency_key)
            return NodeOutcome(update={"analysis": {"modules": []}})

        recorder = InMemoryStepRecorder(
            outcomes={"analyze": ClaimOutcome.ALREADY_SUCCEEDED},
        )
        graph = build(provider, recorder, TutorialNodes(analyze=analyze))
        graph.invoke(initial_state(), config(), durability="sync")
        assert executions == []

    def test_a_node_leased_by_another_worker_is_not_executed(
        self, provider: InMemoryCheckpointerProvider
    ) -> None:
        executions: list[str] = []

        def analyze(state: dict[str, Any], call: Any) -> NodeOutcome:
            executions.append(call.idempotency_key)
            return NodeOutcome(update={"analysis": {"modules": []}})

        recorder = InMemoryStepRecorder(outcomes={"analyze": ClaimOutcome.LEASED_ELSEWHERE})
        graph = build(provider, recorder, TutorialNodes(analyze=analyze))
        graph.invoke(initial_state(), config(), durability="sync")
        assert executions == []

    def test_the_idempotency_key_identifies_the_run_phase_and_node(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        keys = {call.node: call.idempotency_key for call in recorder.calls}
        assert keys["parse"] == "run-1:parse:parse:0"

    def test_replaying_an_approval_does_not_regenerate_the_tutorial(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        graph = build(provider, recorder)
        graph.invoke(initial_state(), config(), durability="sync")
        first = graph.invoke(Command(resume=APPROVE), config(), durability="sync")
        completed = list(recorder.completed_nodes())

        second = graph.invoke(Command(resume=APPROVE), config(), durability="sync")
        assert recorder.completed_nodes() == completed
        assert second["publication"] == first["publication"]


class TestLockedChapters:
    def test_a_locked_chapter_survives_a_partial_regeneration(
        self, provider: InMemoryCheckpointerProvider, recorder: InMemoryStepRecorder
    ) -> None:
        def chapters(state: dict[str, Any], call: Any) -> NodeOutcome:
            drafts = {
                chapter["slug"]: chapter_draft(
                    slug=chapter["slug"],
                    title=f"regenerated {chapter['title']}",
                    ordinal=chapter["ordinal"],
                    status=ChapterStatus.DRAFTED,
                    revision=9,
                )
                for chapter in state["outline"]["chapters"]
            }
            return NodeOutcome(update={"chapters": drafts})

        graph = build(provider, recorder, TutorialNodes(chapters=chapters))
        graph.invoke(initial_state(), config(), durability="sync")

        snapshot = graph.get_state(config())
        locked_slug = snapshot.values["outline"]["chapters"][0]["slug"]
        graph.update_state(
            config(),
            {
                "chapters": {
                    locked_slug: chapter_draft(
                        slug=locked_slug,
                        title="Reviewed by a human",
                        ordinal=0,
                        status=ChapterStatus.LOCKED,
                        locked=True,
                        revision=2,
                    )
                }
            },
        )

        final = graph.invoke(Command(resume=APPROVE), config(), durability="sync")
        assert final["chapters"][locked_slug]["title"] == "Reviewed by a human"
        assert final["chapters"][locked_slug]["revision"] == 2
        other = [slug for slug in final["chapters"] if slug != locked_slug]
        assert all(final["chapters"][slug]["revision"] == 9 for slug in other)
