"""The quality gates are what stand between a generated tutorial and a reader."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tutorial_support import (
    chapter,
    citation,
    code_block,
    diagram,
    diagram_block,
    document,
    fact,
    markdown_block,
    repo_citation,
)

from app.quality import run_quality_gates
from app.tutorial.schema import TutorialDocument


def clean_document(**overrides: object) -> TutorialDocument:
    return document(**overrides)


class TestCitations:
    def test_a_citation_outside_the_approved_scope_fails(self) -> None:
        """The schema rejects this before the gate ever runs; the gate is the report for
        callers that assemble a document from parts before validation."""
        with pytest.raises(ValidationError):
            clean_document(citations=(citation(snapshot_id="snapshot-unapproved"),))

    def test_a_fact_citing_nothing_fails(self) -> None:
        with pytest.raises(ValidationError):
            clean_document(
                chapters=(chapter(facts=(fact(citation_ids=("e-missing",)),)),)
            )

    def test_a_teaching_abstraction_is_not_required_to_cite(self) -> None:
        built = clean_document(
            chapters=(
                chapter(facts=(fact(kind="teaching_abstraction", citation_ids=()),)),
            )
        )

        report = run_quality_gates(built)

        assert not any(f.gate == "citations" for f in report.findings)


class TestCode:
    def test_python_code_that_does_not_parse_fails(self) -> None:
        built = clean_document(
            chapters=(chapter(blocks=(code_block(code="def broken(:"),)),)
        )

        report = run_quality_gates(built)

        assert any(f.code == "code.syntax" for f in report.findings)

    def test_valid_python_code_passes_the_syntax_gate(self) -> None:
        built = clean_document(
            chapters=(
                chapter(blocks=(code_block(code="gateway = build_gateway(config)"),)),
            )
        )

        report = run_quality_gates(built)

        assert not any(f.code == "code.syntax" for f in report.findings)

    def test_typescript_code_that_is_not_illustrative_is_reported_unverified(self) -> None:
        built = clean_document(
            chapters=(
                chapter(
                    blocks=(
                        code_block(
                            language="typescript",
                            code="const g = buildGateway(config)",
                            citation_ids=("e1",),
                        ),
                    )
                ),
            )
        )

        report = run_quality_gates(built)

        assert any(f.code == "code.unverified" for f in report.findings)

    def test_illustrative_typescript_is_not_required_to_be_verified(self) -> None:
        built = clean_document(
            chapters=(
                chapter(
                    blocks=(
                        code_block(
                            language="typescript",
                            code="const g = buildGateway(config)",
                            illustrative=True,
                        ),
                    )
                ),
            )
        )

        report = run_quality_gates(built)

        assert not any(f.code == "code.unverified" for f in report.findings)


class TestDiagrams:
    def test_a_diagram_that_cannot_render_is_reported(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.quality.gates.render_diagram",
            lambda diagram: type(
                "Rendered",
                (),
                {"ok": False, "error": {"stage": "parse", "code": "parse.syntax-error"}},
            )(),
        )
        built = clean_document(chapters=(chapter(blocks=(diagram_block(),)),))

        report = run_quality_gates(built)

        assert any(f.gate == "diagrams" for f in report.findings)

    def test_a_renderer_that_is_not_available_is_a_finding_not_a_crash(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.diagrams import DiagramRenderError

        def unavailable(diagram):
            raise DiagramRenderError("the diagram renderer is not built")

        monkeypatch.setattr("app.quality.gates.render_diagram", unavailable)
        built = clean_document(chapters=(chapter(blocks=(diagram_block(),)),))

        report = run_quality_gates(built)

        assert any(f.code == "diagram.renderer-unavailable" for f in report.findings)


class TestMarkdown:
    def test_an_unclosed_code_fence_fails(self) -> None:
        built = clean_document(
            chapters=(chapter(blocks=(markdown_block(markdown="```python\nprint(1)"),)),)
        )

        report = run_quality_gates(built)

        assert any(f.code == "markdown.unclosed-fence" for f in report.findings)


class TestSecrets:
    def test_a_private_key_marker_is_rejected(self) -> None:
        built = clean_document(
            chapters=(
                chapter(
                    blocks=(
                        markdown_block(markdown="-----BEGIN RSA PRIVATE KEY-----"),
                    )
                ),
            )
        )

        report = run_quality_gates(built)

        assert any(f.gate == "secrets" for f in report.findings)


class TestCleanDocument:
    def test_a_well_formed_document_with_real_evidence_passes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.quality.gates.render_diagram",
            lambda diagram: type(
                "Rendered",
                (),
                {"ok": True, "error": None},
            )(),
        )
        built = clean_document(
            citations=(citation(), repo_citation()),
            chapters=(
                chapter(
                    blocks=(
                        markdown_block(),
                        code_block(
                            code=(
                                "from gateway.factory import build_gateway\n"
                                "gateway = build_gateway(config)"
                            ),
                            citation_ids=("e1",),
                        ),
                        diagram_block(diagram=diagram()),
                    ),
                    facts=(fact(),),
                ),
            ),
        )

        report = run_quality_gates(built)

        assert report.passed
