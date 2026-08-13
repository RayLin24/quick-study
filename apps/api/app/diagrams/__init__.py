"""Deterministic Mermaid generation and rendering for tutorial diagrams.

The model never writes Mermaid by hand. It fills a constrained intermediate
representation in :mod:`app.tutorial.mermaid_ir`, and this package turns that IR into
source with fixed templates, then validates and renders it through the pinned
``packages/diagram-renderer`` CLI.
"""

from app.diagrams.render import DiagramRenderError, render_diagram
from app.diagrams.to_mermaid import diagram_to_mermaid

__all__ = ["DiagramRenderError", "diagram_to_mermaid", "render_diagram"]
