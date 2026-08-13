"""Static analysis of Python source, with honesty about what could not be resolved.

The rule the whole module is written around: an edge is only reported as resolved when the
target was actually found. Python is dynamic enough that a plausible-looking guess is
almost always available, and a tutorial built on guesses is worse than one that says a
relationship could not be determined.
"""

from __future__ import annotations

import pytest

from app.analysis.model import CallResolution, Confidence, ImportResolution, SymbolKind
from app.analysis.python import analyze_python

SERVICE = '''
"""The gateway service."""

from __future__ import annotations

import os
import os.path as ospath
from dataclasses import dataclass

from gateway.config import load_config
from gateway.errors import ConfigError

__all__ = ["Gateway", "build_gateway"]

DEFAULT_PORT = 8443
_INTERNAL = "hidden"

type Handler = str


@dataclass
class Gateway:
    """Routes requests to workers."""

    port: int

    def start(self, backlog: int = 128) -> None:
        """Start listening."""
        self._bind(backlog)

    def _bind(self, backlog: int) -> None:
        os.getpid()


def build_gateway(path: str) -> Gateway:
    """Build a gateway from a configuration file."""
    config = load_config(path)
    return Gateway(port=config.port)


async def serve() -> None:
    gateway = build_gateway("app.toml")
    gateway.start()
'''

CONFIG = '''
class Config:
    port: int = 8443


def load_config(path: str) -> Config:
    return Config()
'''


@pytest.fixture
def analysis():
    return analyze_python(
        {"gateway/service.py": SERVICE, "gateway/config.py": CONFIG},
        root="/repo",
    )


def symbol(analysis, qualified_name: str):
    matches = [item for item in analysis.symbols if item.qualified_name == qualified_name]
    assert matches, f"no symbol named {qualified_name}"
    return matches[0]


def edges_from(analysis, qualified_name: str):
    caller = symbol(analysis, qualified_name)
    return [edge for edge in analysis.call_edges if edge.from_ == caller.id]


class TestFiles:
    def test_every_analysed_file_is_reported_with_its_digest(self, analysis) -> None:
        paths = {item.path for item in analysis.files}

        assert paths == {"gateway/service.py", "gateway/config.py"}
        assert all(len(item.sha256) == 64 for item in analysis.files)
        assert all(item.language == "python" for item in analysis.files)

    def test_a_file_that_does_not_parse_becomes_a_diagnostic_not_a_crash(self) -> None:
        result = analyze_python({"broken.py": "def f(:\n", "fine.py": "def g(): pass\n"})

        broken = next(item for item in result.files if item.path == "broken.py")
        assert broken.syntax_errors
        assert any(item.qualified_name == "g" for item in result.symbols)
        assert any(item.path == "broken.py" for item in result.diagnostics)


class TestSymbols:
    def test_module_level_definitions_are_extracted(self, analysis) -> None:
        assert symbol(analysis, "Gateway").kind is SymbolKind.CLASS
        assert symbol(analysis, "build_gateway").kind is SymbolKind.FUNCTION
        assert symbol(analysis, "DEFAULT_PORT").kind is SymbolKind.VARIABLE
        assert symbol(analysis, "Handler").kind is SymbolKind.TYPE_ALIAS

    def test_a_method_is_qualified_by_its_class(self, analysis) -> None:
        method = symbol(analysis, "Gateway.start")

        assert method.kind is SymbolKind.METHOD
        assert method.parent_id == symbol(analysis, "Gateway").id

    def test_a_symbol_identifier_names_its_file(self, analysis) -> None:
        assert symbol(analysis, "Gateway.start").id == "gateway/service.py#Gateway.start"

    def test_the_declaration_range_is_recorded(self, analysis) -> None:
        method = symbol(analysis, "Gateway.start")

        assert method.range.start_line > 0
        assert method.range.end_line >= method.range.start_line

    def test_the_signature_is_preserved_for_the_reader(self, analysis) -> None:
        assert symbol(analysis, "Gateway.start").signature == (
            "(self, backlog: int = 128) -> None"
        )

    def test_the_first_line_of_the_docstring_is_kept_as_a_summary(self, analysis) -> None:
        assert symbol(analysis, "Gateway").doc_summary == "Routes requests to workers."

    def test_an_async_definition_is_flagged(self, analysis) -> None:
        assert symbol(analysis, "serve").is_async
        assert not symbol(analysis, "build_gateway").is_async

    def test_dunder_all_decides_what_counts_as_exported(self, analysis) -> None:
        assert symbol(analysis, "Gateway").exported
        assert symbol(analysis, "build_gateway").exported
        assert not symbol(analysis, "serve").exported

    def test_a_leading_underscore_is_private_where_there_is_no_dunder_all(self) -> None:
        result = analyze_python({"m.py": "def public(): pass\ndef _private(): pass\n"})

        assert symbol(result, "public").exported
        assert not symbol(result, "_private").exported

    def test_a_name_declared_twice_gets_distinct_identifiers(self) -> None:
        result = analyze_python({"m.py": "def f(): pass\ndef f(): pass\n"})

        assert sorted(item.id for item in result.symbols if item.name == "f") == [
            "m.py#f",
            "m.py#f~2",
        ]

    def test_a_nested_function_is_qualified_the_way_python_qualifies_it(self) -> None:
        result = analyze_python({"m.py": "def outer():\n    def inner(): pass\n"})

        assert any(
            item.qualified_name == "outer.<locals>.inner" for item in result.symbols
        )


class TestImports:
    def test_a_plain_import_is_recorded_as_external(self, analysis) -> None:
        record = next(
            item for item in analysis.imports if item.module_specifier == "os"
        )

        assert record.resolution is ImportResolution.EXTERNAL
        assert record.file == "gateway/service.py"

    def test_an_aliased_import_keeps_both_names(self, analysis) -> None:
        record = next(
            item for item in analysis.imports if item.module_specifier == "os.path"
        )

        assert record.bindings[0].imported == "*"
        assert record.bindings[0].local == "ospath"

    def test_an_import_of_another_analysed_file_resolves_internally(self, analysis) -> None:
        record = next(
            item for item in analysis.imports if item.module_specifier == "gateway.config"
        )

        assert record.resolution is ImportResolution.INTERNAL
        assert record.resolved_file == "gateway/config.py"
        assert [binding.imported for binding in record.bindings] == ["load_config"]

    def test_an_import_of_a_module_that_is_not_present_is_unresolved(self, analysis) -> None:
        record = next(
            item for item in analysis.imports if item.module_specifier == "gateway.errors"
        )

        assert record.resolution is ImportResolution.EXTERNAL

    def test_a_relative_import_is_resolved_against_the_importing_package(self) -> None:
        result = analyze_python(
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from .b import helper\n",
                "pkg/b.py": "def helper(): pass\n",
            }
        )
        record = next(item for item in result.imports if item.file == "pkg/a.py")

        assert record.module_specifier == "pkg.b"
        assert record.resolution is ImportResolution.INTERNAL
        assert record.resolved_file == "pkg/b.py"

    def test_dependencies_summarise_which_files_depend_on_which(self, analysis) -> None:
        internal = {
            (edge.from_, edge.to) for edge in analysis.dependencies if edge.scope == "internal"
        }

        assert ("gateway/service.py", "gateway/config.py") in internal

    def test_external_dependencies_are_reported_by_package(self, analysis) -> None:
        external = {
            edge.to for edge in analysis.dependencies if edge.scope == "external"
        }

        assert "os" in external
        assert "dataclasses" in external


class TestCallEdges:
    def test_a_call_to_a_function_in_the_same_file_is_resolved(self, analysis) -> None:
        edge = next(
            edge for edge in edges_from(analysis, "serve") if edge.callee_name == "build_gateway"
        )

        assert edge.resolution is CallResolution.RESOLVED
        assert edge.confidence is Confidence.HIGH
        assert edge.to == symbol(analysis, "build_gateway").id

    def test_a_call_through_an_internal_import_reaches_the_other_file(self, analysis) -> None:
        edge = next(
            edge
            for edge in edges_from(analysis, "build_gateway")
            if edge.callee_name == "load_config"
        )

        assert edge.resolution is CallResolution.RESOLVED
        assert edge.to == "gateway/config.py#load_config"

    def test_a_call_into_an_external_package_is_marked_external(self, analysis) -> None:
        edge = next(
            edge for edge in edges_from(analysis, "Gateway._bind") if edge.callee_name == "getpid"
        )

        assert edge.resolution is CallResolution.EXTERNAL
        assert edge.confidence is Confidence.MEDIUM
        assert edge.external_module == "os"
        assert edge.to is None

    def test_a_call_on_self_resolves_to_the_class_but_only_at_medium_confidence(
        self, analysis
    ) -> None:
        """A subclass may override the method, so the target is likely, not certain."""
        edge = next(
            edge for edge in edges_from(analysis, "Gateway.start") if edge.callee_name == "_bind"
        )

        assert edge.resolution is CallResolution.RESOLVED
        assert edge.confidence is Confidence.MEDIUM
        assert edge.to == symbol(analysis, "Gateway._bind").id

    def test_a_call_on_a_parameter_cannot_be_resolved(self) -> None:
        result = analyze_python({"m.py": "def run(client):\n    client.get('/a')\n"})

        edge = edges_from(result, "run")[0]
        assert edge.resolution is CallResolution.UNRESOLVED
        assert edge.confidence is Confidence.LOW
        assert edge.to is None
        assert edge.reason

    def test_a_local_name_that_shadows_a_module_function_is_not_resolved_to_it(self) -> None:
        """Reporting the module-level function here would be a fabricated relationship."""
        source = "def handler(): pass\n\ndef run(handler):\n    handler()\n"

        edge = edges_from(analyze_python({"m.py": source}), "run")[0]

        assert edge.resolution is CallResolution.UNRESOLVED
        assert edge.to is None

    def test_a_dynamically_dispatched_call_is_unresolved(self) -> None:
        result = analyze_python({"m.py": "def run(o, n):\n    getattr(o, n)()\n"})
        edges = edges_from(result, "run")

        assert any(
            edge.resolution is CallResolution.UNRESOLVED and "dynamic" in edge.reason
            for edge in edges
        )

    def test_a_builtin_call_is_external_rather_than_invented(self) -> None:
        result = analyze_python({"m.py": "def run(items):\n    return len(items)\n"})

        edge = edges_from(result, "run")[0]
        assert edge.resolution is CallResolution.EXTERNAL
        assert edge.external_module == "builtins"

    def test_a_call_at_module_level_has_no_caller_symbol(self) -> None:
        result = analyze_python({"m.py": "def f(): pass\n\nf()\n"})

        top_level = [edge for edge in result.call_edges if edge.from_ is None]
        assert top_level and top_level[0].from_file == "m.py"

    def test_a_class_instantiation_is_a_call_to_the_class(self, analysis) -> None:
        edge = next(
            edge for edge in edges_from(analysis, "build_gateway") if edge.callee_name == "Gateway"
        )

        assert edge.to == symbol(analysis, "Gateway").id
        assert edge.call_kind == "constructor"

    def test_every_edge_states_its_confidence_and_its_reason(self, analysis) -> None:
        assert analysis.call_edges
        for edge in analysis.call_edges:
            assert edge.reason
            assert isinstance(edge.confidence, Confidence)
            assert (edge.to is not None) == (edge.resolution is CallResolution.RESOLVED)

    def test_nothing_unresolved_is_ever_reported_above_low_confidence(self, analysis) -> None:
        unresolved = [
            edge
            for edge in analysis.call_edges
            if edge.resolution in (CallResolution.UNRESOLVED, CallResolution.AMBIGUOUS)
        ]

        assert all(edge.confidence is Confidence.LOW for edge in unresolved)

    def test_the_call_site_line_is_recorded_so_it_can_be_cited(self, analysis) -> None:
        assert all(edge.range.start_line > 0 for edge in analysis.call_edges)


class TestDeterminismAndLimits:
    def test_analysing_the_same_sources_twice_gives_the_same_document(self) -> None:
        sources = {"gateway/service.py": SERVICE, "gateway/config.py": CONFIG}

        first = analyze_python(sources)
        second = analyze_python(sources)

        assert [item.id for item in first.symbols] == [item.id for item in second.symbols]
        assert [edge.id for edge in first.call_edges] == [edge.id for edge in second.call_edges]

    def test_the_file_count_is_bounded(self) -> None:
        sources = {f"m{index}.py": "def f(): pass\n" for index in range(50)}

        from app.analysis.python import PythonAnalysisLimits

        result = analyze_python(sources, limits=PythonAnalysisLimits(max_files=10))

        assert len(result.files) == 10
        assert result.limits.truncated

    def test_a_file_over_the_size_budget_is_skipped_with_a_diagnostic(self) -> None:
        from app.analysis.python import PythonAnalysisLimits

        result = analyze_python(
            {"big.py": "x = 1\n" * 1000, "small.py": "def f(): pass\n"},
            limits=PythonAnalysisLimits(max_file_bytes=100),
        )

        assert [item.path for item in result.files] == ["small.py"]
        assert any(item.path == "big.py" for item in result.diagnostics)

    def test_the_document_declares_which_tool_produced_it(self, analysis) -> None:
        assert analysis.tool.name
        assert analysis.schema_version
