"""The tutorial generation graph.

``discover -> snapshot -> parse -> index -> analyze -> outline -> human_interrupt ->
chapters -> diagrams -> validate -> publish``, with the one loop the product allows: a
reviewer who rejects the outline sends the run back to ``outline`` and is asked again.

Every node goes through the same wrapper, so the guarantees hold whatever the node does:

* the step is claimed before the work starts, and a node whose step already succeeded or
  is leased by another worker does nothing, which is what makes a redelivered Celery
  message harmless;
* ``pipeline_version``, ``input_hash``, ``prompt_hash``, ``model``, ``attempt``, tokens and
  cost are recorded on success, and the error is recorded on failure;
* the phase in the state moves forward through the reducer that refuses to go backwards.

The graph holds no connection and no configuration of its own: it is given a checkpointer
and a recorder, which is what lets the whole pipeline run in-process in the tests and on
MySQL in a worker.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt

from app.db.models.enums import ApprovalDecision, RunPhase
from app.runs.steps import ClaimOutcome, build_idempotency_key
from app.workflows.tutorial.nodes import NodeFunction, TutorialNodes
from app.workflows.tutorial.recording import (
    InMemoryStepRecorder,
    NodeCall,
    NodeOutcome,
    StepRecorder,
)
from app.workflows.tutorial.state import (
    PIPELINE_VERSION,
    OutlineDraft,
    TutorialState,
    input_digest,
    node_record,
)

INTERRUPT_NODE: Final = "human_interrupt"

#: The pipeline, in order. Each name is also the ``RunPhase`` the node reports.
GRAPH_NODES: Final[tuple[str, ...]] = (
    "discover",
    "snapshot",
    "parse",
    "index",
    "analyze",
    "outline",
    INTERRUPT_NODE,
    "chapters",
    "diagrams",
    "validate",
    "publish",
)

NODE_PHASES: Final[dict[str, RunPhase]] = {name: RunPhase(name) for name in GRAPH_NODES}

#: The slice of state each node reads. Hashing exactly this is what makes ``input_hash``
#: mean "the same inputs" rather than "the same run".
NODE_INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "discover": ("request",),
    "snapshot": ("discovered",),
    "parse": ("snapshots",),
    "index": ("corpus",),
    "analyze": ("index",),
    "outline": ("request", "analysis", "approval"),
    INTERRUPT_NODE: ("outline",),
    "chapters": ("outline",),
    "diagrams": ("chapters",),
    "validate": ("chapters", "diagrams"),
    "publish": ("chapters", "diagrams", "validation"),
}

#: A reviewer who does not approve sends the outline back to be written again.
_APPROVING: Final = frozenset({ApprovalDecision.APPROVED.value})


def build_tutorial_graph(
    *,
    nodes: TutorialNodes | None = None,
    recorder: StepRecorder | None = None,
    pipeline_version: str = PIPELINE_VERSION,
) -> StateGraph:
    """Assemble the graph. Compile it with a checkpointer before running it."""
    bodies = nodes or TutorialNodes()
    ledger = recorder or InMemoryStepRecorder()

    builder: StateGraph = StateGraph(TutorialState)
    for name in GRAPH_NODES:
        if name == INTERRUPT_NODE:
            builder.add_node(name, _approval_gate(ledger, pipeline_version))
        else:
            builder.add_node(
                name, _instrumented(name, bodies.for_node(name), ledger, pipeline_version)
            )

    builder.add_edge(START, GRAPH_NODES[0])
    for source, target in zip(GRAPH_NODES, GRAPH_NODES[1:], strict=False):
        if source == INTERRUPT_NODE:
            continue
        builder.add_edge(source, target)
    builder.add_conditional_edges(
        INTERRUPT_NODE,
        _route_after_approval,
        {"chapters": "chapters", "outline": "outline"},
    )
    builder.add_edge(GRAPH_NODES[-1], END)
    return builder


def compile_tutorial_graph(
    *,
    checkpointer: BaseCheckpointSaver,
    nodes: TutorialNodes | None = None,
    recorder: StepRecorder | None = None,
    pipeline_version: str = PIPELINE_VERSION,
) -> CompiledStateGraph:
    return build_tutorial_graph(
        nodes=nodes, recorder=recorder, pipeline_version=pipeline_version
    ).compile(checkpointer=checkpointer)


def approval_request(state: TutorialState) -> dict[str, Any]:
    """What a reviewer is shown while the run is suspended."""
    return {
        "kind": "outline_approval",
        "run_id": state["run_id"],
        "project_id": state["project_id"],
        "outline": state["outline"],
    }


def _instrumented(
    name: str,
    body: NodeFunction,
    recorder: StepRecorder,
    pipeline_version: str,
) -> Any:
    phase = NODE_PHASES[name]

    def run(state: TutorialState) -> dict[str, Any]:
        entry = state.get("attempts", {}).get(name, 0)
        claimed = recorder.claim(
            node=name,
            phase=phase,
            run_id=state["run_id"],
            project_id=state["project_id"],
            pipeline_version=pipeline_version,
            input_hash=_input_hash(name, state, pipeline_version),
            idempotency_key=build_idempotency_key(
                state["run_id"], phase.value, name, str(entry)
            ),
        )
        if claimed.outcome is not ClaimOutcome.CLAIMED:
            # Another delivery already did this, or is doing it right now. Its result is
            # in the checkpoint; repeating the side effect is the one thing we must not do.
            return {}

        call = claimed.call
        try:
            outcome = body(state, call)
        except Exception as error:
            recorder.failed(call, error)
            raise
        recorder.succeeded(call, outcome)
        return _state_update(name, phase, call, outcome, entry)

    return run


def _approval_gate(recorder: StepRecorder, pipeline_version: str) -> Any:
    """Suspend the run until a human decides, then record the decision like any node.

    ``interrupt`` is raised before anything is claimed, so the pass that stops the run
    leaves no half-finished step behind; the recording happens on the pass that resumes.
    """

    def gate(state: TutorialState) -> dict[str, Any]:
        decision = interrupt(approval_request(state))

        def apply(current: TutorialState, call: NodeCall) -> NodeOutcome:
            return NodeOutcome(update=_apply_decision(current, decision))

        return _instrumented(INTERRUPT_NODE, apply, recorder, pipeline_version)(state)

    return gate


def _apply_decision(state: TutorialState, decision: Any) -> dict[str, Any]:
    payload: Mapping[str, Any] = decision if isinstance(decision, Mapping) else {}
    outline = state["outline"]
    update: dict[str, Any] = {
        "approval": {
            "decision": ApprovalDecision(
                payload.get("decision", ApprovalDecision.APPROVED.value)
            ).value,
            "note": str(payload.get("note", "")),
            "decided_by": payload.get("decided_by"),
            "outline_version": outline["version"],
        }
    }
    edited = payload.get("outline")
    if isinstance(edited, Mapping):
        update["outline"] = _merge_reviewer_edits(outline, edited)
    return update


def _merge_reviewer_edits(
    outline: OutlineDraft, edited: Mapping[str, Any]
) -> OutlineDraft:
    """An approved outline is whatever the reviewer approved, not what was proposed."""
    chapters = edited.get("chapters", outline["chapters"])
    return {
        "version": outline["version"],
        "title": str(edited.get("title", outline["title"])),
        "summary": str(edited.get("summary", outline["summary"])),
        "chapters": [
            {
                "slug": chapter["slug"],
                "title": chapter["title"],
                "ordinal": chapter.get("ordinal", ordinal),
                "summary": chapter.get("summary", ""),
            }
            for ordinal, chapter in enumerate(chapters)
        ],
    }


def _route_after_approval(state: TutorialState) -> str:
    approval = state.get("approval")
    if approval is None or approval["decision"] not in _APPROVING:
        return "outline"
    return "chapters"


def _state_update(
    name: str,
    phase: RunPhase,
    call: NodeCall,
    outcome: NodeOutcome,
    entry: int,
) -> dict[str, Any]:
    return {
        **outcome.update,
        "phase": phase.value,
        "attempts": {name: entry + 1},
        "records": [
            node_record(
                node=name,
                phase=phase,
                input_hash=call.input_hash,
                attempt=call.attempt,
                prompt_hash=outcome.prompt_hash,
                model=outcome.model,
                tokens_in=outcome.tokens_in,
                tokens_out=outcome.tokens_out,
                cost_usd=outcome.cost_usd,
                idempotency_key=call.idempotency_key,
            )
        ],
        "usage": {
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "cost_usd": str(outcome.cost_usd),
        },
    }


def _input_hash(name: str, state: TutorialState, pipeline_version: str) -> str:
    return input_digest(
        {
            "pipeline_version": pipeline_version,
            "node": name,
            "inputs": {key: state.get(key) for key in NODE_INPUTS[name]},
        }
    )
