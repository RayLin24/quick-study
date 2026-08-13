"""A constrained intermediate representation for the diagrams a tutorial contains.

The model is never asked to write Mermaid. It fills in this structure and a deterministic
template renders the diagram source, which moves every class of "the model wrote something
that does not parse" from a rendering failure into a validation failure with a precise
message. It also makes a diagram reviewable in the same terms as prose: a node or an edge
either cites the evidence it came from or is explicitly labelled a teaching simplification.

The limits are editorial, not technical. A twenty-node architecture diagram is already past
what a reader can follow, and the cap is what stops a model from emitting the whole
call graph.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Final, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_NODES: Final = 24
MAX_EDGES: Final = 48
MAX_LABEL_LENGTH: Final = 80

#: Mermaid node ids are bare identifiers in the diagram source, so they are restricted to
#: what every Mermaid dialect parses the same way.
NODE_ID_PATTERN: Final = r"^[A-Za-z][A-Za-z0-9_]{0,31}$"

SLUG_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"

#: Words Mermaid treats as syntax. ``end`` is the notorious one: used as a node id it
#: terminates the enclosing block and corrupts the rest of the diagram.
_RESERVED_IDS: Final = frozenset(
    {
        "class",
        "classdef",
        "click",
        "end",
        "flowchart",
        "graph",
        "linkstyle",
        "sequencediagram",
        "style",
        "subgraph",
    }
)

#: Characters that either break the diagram source or carry markup into the rendered SVG.
#: The renderer sanitises again; keeping them out of the IR means the sanitiser never has
#: to silently change what a reviewer approved.
_UNSAFE_LABEL_CHARS: Final = frozenset("<>`;{}|\"\\\n\r\t")

NodeId = Annotated[str, StringConstraints(pattern=NODE_ID_PATTERN)]
Slug = Annotated[str, StringConstraints(pattern=SLUG_PATTERN, max_length=160)]
Label = Annotated[str, StringConstraints(max_length=MAX_LABEL_LENGTH)]


class DiagramKind(StrEnum):
    """The three diagram types this system generates, per the product boundary."""

    ARCHITECTURE = "architecture"
    FLOW = "flow"
    SEQUENCE = "sequence"


class DiagramDirection(StrEnum):
    LEFT_RIGHT = "left_right"
    TOP_DOWN = "top_down"


class NodeRole(StrEnum):
    """What a node stands for, which the template turns into a shape."""

    ACTOR = "actor"
    SERVICE = "service"
    COMPONENT = "component"
    DATASTORE = "datastore"
    EXTERNAL = "external"
    PROCESS = "process"
    DECISION = "decision"
    TERMINAL = "terminal"


class EdgeKind(StrEnum):
    FLOW = "flow"
    CALL = "call"
    RETURN = "return"
    DEPENDENCY = "dependency"
    DATA = "data"


#: Roles that make sense as a lifeline in a sequence diagram. A decision diamond does not.
_SEQUENCE_ROLES: Final = frozenset(
    {NodeRole.ACTOR, NodeRole.SERVICE, NodeRole.COMPONENT, NodeRole.DATASTORE, NodeRole.EXTERNAL}
)


class EvidenceBacked(BaseModel):
    """Base for anything in a diagram that asserts something about the subject."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_ids: tuple[str, ...] = ()
    teaching_abstraction: bool = False

    @model_validator(mode="after")
    def _grounded_or_labelled(self) -> Self:
        if not self.citation_ids and not self.teaching_abstraction:
            raise ValueError(
                "cite at least one evidence id, or set teaching_abstraction when this is a "
                "simplification invented for the reader"
            )
        return self


class DiagramNode(EvidenceBacked):
    id: NodeId
    label: Label
    role: NodeRole = NodeRole.COMPONENT

    @model_validator(mode="after")
    def _usable_in_mermaid(self) -> Self:
        if self.id.lower() in _RESERVED_IDS:
            raise ValueError(f"{self.id!r} is Mermaid syntax and cannot be a node id")
        _require_safe_label(self.label, required=True)
        return self

    @model_validator(mode="before")
    @classmethod
    def _trim_label(cls, data: object) -> object:
        return _trim(data, "label")


class DiagramEdge(EvidenceBacked):
    source: NodeId
    target: NodeId
    label: Label = ""
    kind: EdgeKind = EdgeKind.FLOW

    @property
    def signature(self) -> tuple[str, str, str, str]:
        return (self.source, self.target, self.label, self.kind.value)

    @model_validator(mode="after")
    def _usable_in_mermaid(self) -> Self:
        _require_safe_label(self.label, required=False)
        return self

    @model_validator(mode="before")
    @classmethod
    def _trim_label(cls, data: object) -> object:
        return _trim(data, "label")


class MermaidDiagram(BaseModel):
    """One diagram, ready for a deterministic template to render."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: Slug
    kind: DiagramKind
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    direction: DiagramDirection = DiagramDirection.LEFT_RIGHT
    nodes: Annotated[tuple[DiagramNode, ...], Field(min_length=2, max_length=MAX_NODES)]
    edges: Annotated[tuple[DiagramEdge, ...], Field(min_length=1, max_length=MAX_EDGES)]
    caption: Annotated[str, StringConstraints(max_length=500)] = ""

    def citation_ids(self) -> frozenset[str]:
        """Every evidence id the diagram references, for document-level resolution."""
        return frozenset(
            citation_id
            for element in (*self.nodes, *self.edges)
            for citation_id in element.citation_ids
        )

    def node_ids(self) -> tuple[str, ...]:
        return tuple(node.id for node in self.nodes)

    @model_validator(mode="after")
    def _consistent_graph(self) -> Self:
        seen: set[str] = set()
        for node in self.nodes:
            if node.id in seen:
                raise ValueError(f"duplicate node id {node.id!r}")
            seen.add(node.id)

        signatures: set[tuple[str, str, str, str]] = set()
        for edge in self.edges:
            for endpoint in (edge.source, edge.target):
                if endpoint not in seen:
                    raise ValueError(f"edge endpoint {endpoint!r} is not a node in this diagram")
            if edge.signature in signatures:
                raise ValueError(f"duplicate edge {edge.source!r} -> {edge.target!r}")
            signatures.add(edge.signature)

        if self.kind is DiagramKind.SEQUENCE:
            for node in self.nodes:
                if node.role not in _SEQUENCE_ROLES:
                    raise ValueError(
                        f"a sequence diagram participant cannot be a {node.role.value} node"
                    )
        return self


def _require_safe_label(label: str, *, required: bool) -> None:
    if required and not label.strip():
        raise ValueError("a node needs a visible label")
    unsafe = sorted(_UNSAFE_LABEL_CHARS & set(label))
    if unsafe:
        rendered = ", ".join(repr(character) for character in unsafe)
        raise ValueError(f"label may not contain {rendered}")


def _trim(data: object, key: str) -> object:
    """Trim surrounding whitespace, which is a formatting slip rather than an error.

    Interior whitespace is left alone so an unsafe character is reported instead of being
    silently rewritten into something a reviewer never saw.
    """
    if isinstance(data, dict) and isinstance(data.get(key), str):
        return {**data, key: data[key].strip()}
    return data
