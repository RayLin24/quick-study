"""Validate and render Mermaid through the pinned renderer CLI.

The renderer is a separate Node process because Mermaid owns global state and a DOM. The
CLI contract is the one place that boundary is defined: a rejected diagram exits 2 with a
full JSON result on stdout, and an internal failure exits 3 with JSON on stderr. This
module is the only Python code that knows either.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from app.diagrams.to_mermaid import diagram_to_mermaid
from app.settings import REPO_ROOT
from app.tutorial.mermaid_ir import MermaidDiagram

#: Hard outer timeout. Mermaid's layout is synchronous and cannot be pre-empted, so the
#: process timeout is the real backstop; the CLI's render budget only bounds the wait.
RENDER_TIMEOUT_SECONDS: Final = 30


class DiagramRenderError(Exception):
    """Raised when the renderer could not be invoked or its result could not be read."""

    def __init__(self, message: str, *, detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.detail = detail or {}


@dataclass(frozen=True, slots=True)
class RenderedDiagram:
    """The outcome of rendering one diagram."""

    ok: bool
    mermaid: str
    svg: str | None = None
    diagram_type: str | None = None
    error: dict[str, Any] | None = None


def render_diagram(diagram: MermaidDiagram) -> RenderedDiagram:
    """Validate and render ``diagram``, returning a result rather than raising on a bad diagram.

    A diagram that fails validation or rendering is a *verdict*, not a crash: the caller
    gets the structured error and can repair or degrade. Only a broken renderer, a missing
    executable or an unreadable result raises :class:`DiagramRenderError`.
    """
    source = diagram_to_mermaid(diagram)
    result = _invoke_renderer(source, diagram.slug)
    return RenderedDiagram(
        ok=result.get("ok") is True,
        mermaid=source,
        svg=result.get("svg"),
        diagram_type=result.get("diagramType"),
        error=result.get("error"),
    )


def _invoke_renderer(source: str, diagram_id: str) -> dict[str, Any]:
    cli = _renderer_cli()
    node = _node_executable()
    if cli is None or node is None:
        raise DiagramRenderError("the diagram renderer is not built")
    try:
        completed = subprocess.run(  # noqa: S603 - fixed executable and arguments, no shell
            [node, str(cli), "--input", "-", "--id", diagram_id],
            input=source,
            capture_output=True,
            text=True,
            timeout=RENDER_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as error:
        raise DiagramRenderError("node is not available") from error
    except subprocess.TimeoutExpired as error:
        raise DiagramRenderError("diagram rendering timed out") from error

    if completed.returncode in (0, 2):
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise DiagramRenderError("renderer returned unreadable JSON") from error
    try:
        detail = json.loads(completed.stderr)
    except json.JSONDecodeError:
        detail = {"stderr": completed.stderr}
    raise DiagramRenderError("the diagram renderer failed", detail=detail)


def _renderer_cli() -> Path | None:
    """Return the built renderer CLI, or nothing when it has not been built."""
    candidate = REPO_ROOT / "packages" / "diagram-renderer" / "dist" / "cli.js"
    if _node_executable() is None:
        return None
    return candidate if candidate.is_file() else None


def _node_executable() -> str | None:
    """Resolve the Node executable once so subprocess never searches a bare name."""
    return shutil.which("node")
