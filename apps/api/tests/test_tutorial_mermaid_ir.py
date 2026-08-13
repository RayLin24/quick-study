"""The diagram intermediate representation.

The model never writes Mermaid. It fills in this structure, and a deterministic template
turns it into diagram source later. That split is what makes a generated diagram
reviewable: the shape is constrained here, so the only failures left downstream are
rendering ones, and a node that claims something about the system has to point at the
evidence for it or be labelled as a teaching simplification.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tutorial_support import diagram

from app.tutorial.mermaid_ir import (
    MAX_EDGES,
    MAX_LABEL_LENGTH,
    MAX_NODES,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    EdgeKind,
    MermaidDiagram,
    NodeRole,
)


def node(node_id: str = "Gateway", **overrides: object) -> DiagramNode:
    payload: dict[str, object] = {
        "id": node_id,
        "label": node_id,
        "role": NodeRole.SERVICE,
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return DiagramNode(**payload)  # type: ignore[arg-type]


def edge(source: str = "A", target: str = "B", **overrides: object) -> DiagramEdge:
    payload: dict[str, object] = {
        "source": source,
        "target": target,
        "label": "calls",
        "kind": EdgeKind.CALL,
        "citation_ids": ("e1",),
    }
    payload.update(overrides)
    return DiagramEdge(**payload)  # type: ignore[arg-type]


class TestEvidenceRequirement:
    def test_a_node_must_cite_evidence_or_admit_it_is_a_simplification(self) -> None:
        with pytest.raises(ValidationError) as failure:
            node(citation_ids=())

        assert "teaching" in str(failure.value).lower()

    def test_an_uncited_node_is_allowed_once_it_is_labelled_as_teaching(self) -> None:
        simplified = node(citation_ids=(), teaching_abstraction=True)

        assert simplified.teaching_abstraction

    def test_an_edge_is_held_to_the_same_standard(self) -> None:
        with pytest.raises(ValidationError):
            edge(citation_ids=())

    def test_the_referenced_citation_ids_can_be_collected_for_resolution(self) -> None:
        drawing = diagram()

        assert drawing.citation_ids() == frozenset({"e1", "e2"})


class TestNodeIdentifiers:
    @pytest.mark.parametrize("identifier", ["1gateway", "gate-way", "gate way", "", "gate;way"])
    def test_an_identifier_that_mermaid_cannot_parse_is_refused(self, identifier: str) -> None:
        with pytest.raises(ValidationError):
            node(identifier)

    @pytest.mark.parametrize("identifier", ["end", "graph", "subgraph", "class", "click", "style"])
    def test_a_mermaid_keyword_is_refused_as_an_identifier(self, identifier: str) -> None:
        """``end`` in particular parses as a block terminator and breaks the whole diagram."""
        with pytest.raises(ValidationError):
            node(identifier)

    def test_a_normal_identifier_is_accepted(self) -> None:
        assert node("Gateway_2").id == "Gateway_2"


class TestLabels:
    def test_a_label_may_not_span_lines(self) -> None:
        with pytest.raises(ValidationError):
            node(label="Gateway\nservice")

    @pytest.mark.parametrize("label", ["<script>alert(1)</script>", "Gateway `code`", "a;b"])
    def test_a_label_that_could_escape_the_diagram_is_refused(self, label: str) -> None:
        with pytest.raises(ValidationError):
            node(label=label)

    def test_a_label_is_length_bounded_so_a_diagram_stays_readable(self) -> None:
        with pytest.raises(ValidationError):
            node(label="x" * (MAX_LABEL_LENGTH + 1))

    def test_a_node_needs_a_label(self) -> None:
        with pytest.raises(ValidationError):
            node(label="   ")

    def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert node(label="  Gateway  ").label == "Gateway"

    def test_an_edge_label_may_be_empty(self) -> None:
        assert edge(label="").label == ""


class TestDiagramStructure:
    def test_a_diagram_needs_at_least_two_nodes_and_one_edge(self) -> None:
        with pytest.raises(ValidationError):
            MermaidDiagram(
                slug="lonely",
                kind=DiagramKind.FLOW,
                title="Lonely",
                nodes=(node("A"),),
                edges=(),
            )

    def test_duplicate_node_identifiers_are_refused(self) -> None:
        with pytest.raises(ValidationError) as failure:
            diagram(nodes=(node("A"), node("A")), edges=(edge("A", "A"),))

        assert "duplicate" in str(failure.value).lower()

    def test_an_edge_pointing_at_a_node_that_does_not_exist_is_refused(self) -> None:
        with pytest.raises(ValidationError) as failure:
            diagram(edges=(edge("Gateway", "Nowhere"),))

        assert "Nowhere" in str(failure.value)

    def test_the_same_edge_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            diagram(
                nodes=(node("A"), node("B")),
                edges=(edge("A", "B"), edge("A", "B")),
            )

    def test_the_node_count_is_capped(self) -> None:
        nodes = tuple(node(f"N{index}") for index in range(MAX_NODES + 1))

        with pytest.raises(ValidationError):
            diagram(nodes=nodes, edges=(edge("N0", "N1"),))

    def test_the_edge_count_is_capped(self) -> None:
        nodes = tuple(node(f"N{index}") for index in range(4))
        edges = tuple(
            edge("N0", "N1", label=f"call {index}") for index in range(MAX_EDGES + 1)
        )

        with pytest.raises(ValidationError):
            diagram(nodes=nodes, edges=edges)

    def test_a_slug_is_a_usable_file_and_anchor_name(self) -> None:
        with pytest.raises(ValidationError):
            diagram(slug="Gateway Architecture!")


class TestSequenceDiagrams:
    def test_a_sequence_diagram_only_holds_participants(self) -> None:
        """A decision diamond has no meaning between two lifelines."""
        with pytest.raises(ValidationError) as failure:
            diagram(
                kind=DiagramKind.SEQUENCE,
                nodes=(node("Client", role=NodeRole.ACTOR), node("Gate", role=NodeRole.DECISION)),
                edges=(edge("Client", "Gate"),),
            )

        assert "sequence" in str(failure.value).lower()

    def test_a_valid_sequence_diagram_is_accepted(self) -> None:
        drawing = diagram(
            kind=DiagramKind.SEQUENCE,
            nodes=(node("Client", role=NodeRole.ACTOR), node("Gateway")),
            edges=(
                edge("Client", "Gateway", label="request"),
                edge("Gateway", "Client", label="response", kind=EdgeKind.RETURN),
            ),
        )

        assert drawing.kind is DiagramKind.SEQUENCE
        assert len(drawing.edges) == 2


class TestSerialisation:
    def test_a_diagram_round_trips_through_json_unchanged(self) -> None:
        original = diagram()

        assert MermaidDiagram.model_validate_json(original.model_dump_json()) == original

    def test_an_unknown_field_is_refused_so_output_drift_is_caught(self) -> None:
        payload = diagram().model_dump(mode="json")
        payload["mermaid_source"] = "flowchart LR"

        with pytest.raises(ValidationError):
            MermaidDiagram.model_validate(payload)
