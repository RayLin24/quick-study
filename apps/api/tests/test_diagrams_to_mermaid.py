"""The IR-to-Mermaid templates are the only place Mermaid syntax is chosen."""

from __future__ import annotations

import pytest

from app.diagrams.to_mermaid import diagram_to_mermaid
from app.tutorial.mermaid_ir import (
    DiagramDirection,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    EdgeKind,
    MermaidDiagram,
    NodeRole,
)


def flow_diagram(**overrides: object) -> MermaidDiagram:
    payload: dict[str, object] = {
        "slug": "gateway-flow",
        "kind": DiagramKind.FLOW,
        "title": "Gateway flow",
        "direction": DiagramDirection.LEFT_RIGHT,
        "nodes": (
            DiagramNode(id="Client", label="Client", role=NodeRole.ACTOR, citation_ids=("e1",)),
            DiagramNode(id="Gateway", label="Gateway", role=NodeRole.SERVICE, citation_ids=("e1",)),
        ),
        "edges": (
            DiagramEdge(source="Client", target="Gateway", label="requests", citation_ids=("e1",)),
        ),
    }
    payload.update(overrides)
    return MermaidDiagram(**payload)  # type: ignore[arg-type]


class TestFlowcharts:
    def test_a_flowchart_renders_nodes_before_edges(self) -> None:
        source = diagram_to_mermaid(flow_diagram())

        assert source == (
            "flowchart LR\n"
            "  Client([Client])\n"
            "  Gateway[Gateway]\n"
            "  Client -->|requests| Gateway\n"
        )

    def test_the_direction_is_translated(self) -> None:
        source = diagram_to_mermaid(flow_diagram(direction=DiagramDirection.TOP_DOWN))

        assert source.startswith("flowchart TD\n")

    def test_a_datastore_uses_a_cylinder_shape(self) -> None:
        built = flow_diagram(
            nodes=(
                DiagramNode(
                    id="Worker", label="Worker", role=NodeRole.SERVICE, citation_ids=("e1",)
                ),
                DiagramNode(
                    id="Queue", label="Queue", role=NodeRole.DATASTORE, citation_ids=("e1",)
                ),
            ),
            edges=(
                DiagramEdge(source="Worker", target="Queue", citation_ids=("e1",)),
            ),
        )

        assert "Queue[(Queue)]" in diagram_to_mermaid(built)

    def test_a_dependency_uses_a_dotted_arrow(self) -> None:
        built = flow_diagram(
            edges=(
                DiagramEdge(
                    source="Client",
                    target="Gateway",
                    kind=EdgeKind.DEPENDENCY,
                    citation_ids=("e1",),
                ),
            )
        )

        assert "Client -.-> Gateway" in diagram_to_mermaid(built)

    def test_an_edge_without_a_label_has_no_label_bar(self) -> None:
        built = flow_diagram(
            edges=(DiagramEdge(source="Client", target="Gateway", citation_ids=("e1",)),)
        )

        assert "Client --> Gateway" in diagram_to_mermaid(built)


class TestSequences:
    def test_a_sequence_diagram_renders_participants_then_messages(self) -> None:
        built = MermaidDiagram(
            slug="request-sequence",
            kind=DiagramKind.SEQUENCE,
            title="Request sequence",
            nodes=(
                DiagramNode(
                    id="Client", label="Client", role=NodeRole.ACTOR, citation_ids=("e1",)
                ),
                DiagramNode(
                    id="Gateway", label="Gateway", role=NodeRole.SERVICE, citation_ids=("e1",)
                ),
            ),
            edges=(
                DiagramEdge(
                    source="Client", target="Gateway", label="GET /guide", citation_ids=("e1",)
                ),
                DiagramEdge(
                    source="Gateway",
                    target="Client",
                    label="200",
                    kind=EdgeKind.RETURN,
                    citation_ids=("e1",),
                ),
            ),
        )

        source = diagram_to_mermaid(built)

        assert source == (
            "sequenceDiagram\n"
            "  participant Client as Client\n"
            "  participant Gateway as Gateway\n"
            "  Client->>Gateway: GET /guide\n"
            "  Gateway-->>Client: 200\n"
        )


class TestDeterminism:
    def test_the_same_ir_renders_the_same_bytes(self) -> None:
        assert diagram_to_mermaid(flow_diagram()) == diagram_to_mermaid(flow_diagram())

    def test_node_and_edge_order_are_the_input_order(self) -> None:
        built = flow_diagram()
        first = diagram_to_mermaid(built)
        second = diagram_to_mermaid(built.model_copy(update={"title": "Renamed"}))

        assert first == second


class TestEvidence:
    def test_a_node_without_evidence_must_be_labelled_a_teaching_abstraction(self) -> None:
        with pytest.raises(ValueError):
            DiagramNode(id="Cache", label="Cache", role=NodeRole.DATASTORE)

    def test_a_teaching_abstraction_is_allowed_without_evidence(self) -> None:
        node = DiagramNode(
            id="Cache",
            label="Cache",
            role=NodeRole.DATASTORE,
            teaching_abstraction=True,
        )

        assert node.teaching_abstraction is True
