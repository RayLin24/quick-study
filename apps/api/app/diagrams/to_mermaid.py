"""Turn a constrained diagram IR into Mermaid source.

Deterministic on purpose: the same IR always produces the same bytes, so a diagram can be
content-addressed and a re-run recognised as a no-op. The model's job stops at filling the
IR; every choice about Mermaid syntax lives here.
"""

from __future__ import annotations

from app.tutorial.mermaid_ir import (
    DiagramDirection,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    EdgeKind,
    MermaidDiagram,
    NodeRole,
)

_DIRECTION = {
    DiagramDirection.LEFT_RIGHT: "LR",
    DiagramDirection.TOP_DOWN: "TD",
}

_NODE_SHAPES = {
    NodeRole.ACTOR: ("([", "])"),
    NodeRole.SERVICE: ("[", "]"),
    NodeRole.COMPONENT: ("[", "]"),
    NodeRole.DATASTORE: ("[(", ")]"),
    NodeRole.EXTERNAL: ("[[", "]]"),
    NodeRole.PROCESS: ("[", "]"),
    NodeRole.DECISION: ("{", "}"),
    NodeRole.TERMINAL: ("([", "])"),
}

_EDGE_SYNTAX = {
    EdgeKind.FLOW: "-->",
    EdgeKind.CALL: "-->",
    EdgeKind.RETURN: "-->",
    EdgeKind.DEPENDENCY: "-.->",
    EdgeKind.DATA: "-->",
}


def diagram_to_mermaid(diagram: MermaidDiagram) -> str:
    """Render ``diagram`` to Mermaid source, deterministically."""
    if diagram.kind is DiagramKind.SEQUENCE:
        return _sequence(diagram)
    return _flowchart(diagram)


def _flowchart(diagram: MermaidDiagram) -> str:
    lines = [f"flowchart {_DIRECTION[diagram.direction]}"]
    for node in diagram.nodes:
        lines.append(f"  {_node(node)}")
    for edge in diagram.edges:
        lines.append(f"  {_edge(edge)}")
    return "\n".join(lines) + "\n"


def _node(node: DiagramNode) -> str:
    open_shape, close_shape = _NODE_SHAPES[node.role]
    return f"{node.id}{open_shape}{node.label}{close_shape}"


def _edge(edge: DiagramEdge) -> str:
    arrow = _EDGE_SYNTAX[edge.kind]
    if edge.label:
        return f"{edge.source} {arrow}|{edge.label}| {edge.target}"
    return f"{edge.source} {arrow} {edge.target}"


def _sequence(diagram: MermaidDiagram) -> str:
    lines = ["sequenceDiagram"]
    for node in diagram.nodes:
        lines.append(f"  participant {node.id} as {node.label}")
    for edge in diagram.edges:
        arrow = "-->>" if edge.kind is EdgeKind.RETURN else "->>"
        label = f": {edge.label}" if edge.label else ""
        lines.append(f"  {edge.source}{arrow}{edge.target}{label}")
    return "\n".join(lines) + "\n"
