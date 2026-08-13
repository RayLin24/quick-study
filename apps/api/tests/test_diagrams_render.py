"""Rendering a diagram through the pinned CLI is a verdict, not a crash."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app.diagrams.render import DiagramRenderError, render_diagram
from app.tutorial.mermaid_ir import (
    DiagramDirection,
    DiagramEdge,
    DiagramKind,
    DiagramNode,
    MermaidDiagram,
    NodeRole,
)


def valid_diagram(**overrides: object) -> MermaidDiagram:
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


@pytest.fixture
def renderer_available(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    cli = tmp_path / "cli.js"
    cli.write_text("// stub", encoding="utf-8")
    monkeypatch.setattr("app.diagrams.render._renderer_cli", lambda: cli)
    return cli


def completed(payload: dict[str, object], *, code: int = 0, stderr: str = "") -> object:
    return subprocess.CompletedProcess(
        args=[], returncode=code, stdout=json.dumps(payload), stderr=stderr
    )


class TestSuccessfulRender:
    def test_a_valid_diagram_returns_sanitised_svg(
        self, monkeypatch: pytest.MonkeyPatch, renderer_available: Path
    ) -> None:
        payload = {"ok": True, "svg": "<svg></svg>", "diagramType": "flowchart-v2", "error": None}
        monkeypatch.setattr(
            "app.diagrams.render.subprocess.run",
            lambda *args, **kwargs: completed(payload),
        )

        rendered = render_diagram(valid_diagram())

        assert rendered.ok is True
        assert rendered.svg == "<svg></svg>"
        assert rendered.diagram_type == "flowchart-v2"

    def test_the_cli_receives_the_id_and_source_on_stdin(
        self, monkeypatch: pytest.MonkeyPatch, renderer_available: Path
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run(*args: object, **kwargs: object) -> object:
            captured["argv"] = args[0]
            captured.update(kwargs)
            return completed({"ok": True, "svg": "<svg/>", "diagramType": "flowchart-v2"})

        monkeypatch.setattr("app.diagrams.render.subprocess.run", fake_run)

        render_diagram(valid_diagram())

        assert captured["input"].startswith("flowchart LR")
        assert "--id" in captured["argv"]
        assert "gateway-flow" in captured["argv"]


class TestRejectedDiagram:
    def test_exit_code_two_is_a_verdict_with_a_diagnosable_error(
        self, monkeypatch: pytest.MonkeyPatch, renderer_available: Path
    ) -> None:
        payload = {
            "ok": False,
            "svg": None,
            "diagramType": None,
            "error": {"stage": "parse", "code": "parse.syntax-error", "line": 2},
        }
        monkeypatch.setattr(
            "app.diagrams.render.subprocess.run", lambda *args, **kwargs: completed(payload, code=2)
        )

        rendered = render_diagram(valid_diagram())

        assert rendered.ok is False
        assert rendered.svg is None
        assert rendered.error["stage"] == "parse"


class TestRendererFailures:
    def test_a_missing_renderer_is_a_configuration_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.diagrams.render._renderer_cli", lambda: None)

        with pytest.raises(DiagramRenderError):
            render_diagram(valid_diagram())

    def test_a_timeout_is_a_render_error(
        self, monkeypatch: pytest.MonkeyPatch, renderer_available: Path
    ) -> None:
        def timeout(*args: object, **kwargs: object) -> object:
            raise subprocess.TimeoutExpired(cmd="node", timeout=30)

        monkeypatch.setattr("app.diagrams.render.subprocess.run", timeout)

        with pytest.raises(DiagramRenderError):
            render_diagram(valid_diagram())

    def test_unreadable_json_is_a_render_error(
        self, monkeypatch: pytest.MonkeyPatch, renderer_available: Path
    ) -> None:
        monkeypatch.setattr(
            "app.diagrams.render.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=[], returncode=0, stdout="not-json", stderr=""
            ),
        )

        with pytest.raises(DiagramRenderError):
            render_diagram(valid_diagram())
