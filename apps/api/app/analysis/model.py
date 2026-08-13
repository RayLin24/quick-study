"""The one analysis document every language produces.

Field names and enum values mirror the JSON contract of ``@quick-study/ts-analyzer``, so
its output parses into these structures unchanged and the Python analyser produces
something a caller cannot tell apart. Anything downstream — the symbol index, the
dependency graph, the architecture diagram — reads one shape.

The honesty rules live in the types themselves. A call edge carries a ``resolution``, a
``confidence`` and a machine-readable ``reason``, and ``to`` is populated only when the
target is a symbol that also appears in ``symbols``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Any, Final, Self

SCHEMA_VERSION: Final = "1.0.0"


class SymbolKind(StrEnum):
    """What a definition is. Values are shared with the TypeScript analyser."""

    MODULE = "module"
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    CONSTRUCTOR = "constructor"
    GETTER = "getter"
    SETTER = "setter"
    PROPERTY = "property"
    VARIABLE = "variable"
    INTERFACE = "interface"
    TYPE_ALIAS = "type-alias"
    ENUM = "enum"
    NAMESPACE = "namespace"


class CallResolution(StrEnum):
    """How firmly a call's target was established."""

    RESOLVED = "resolved"
    EXTERNAL = "external"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class Confidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ImportKind(StrEnum):
    STATIC = "static"
    DYNAMIC = "dynamic"
    REQUIRE = "require"
    RE_EXPORT = "re-export"


class ImportResolution(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    UNRESOLVED = "unresolved"


class BindingKind(StrEnum):
    NAMED = "named"
    DEFAULT = "default"
    NAMESPACE = "namespace"


class DiagnosticSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


#: How a qualitative confidence maps onto the numeric ``edges.confidence`` column.
CONFIDENCE_SCORES: Final[dict[Confidence, Decimal]] = {
    Confidence.HIGH: Decimal("0.950"),
    Confidence.MEDIUM: Decimal("0.600"),
    Confidence.LOW: Decimal("0.200"),
}


@dataclass(frozen=True, slots=True)
class SourceRange:
    """A 1-based, inclusive span of source text."""

    start_line: int = 0
    start_column: int = 0
    end_line: int = 0
    end_column: int = 0

    @classmethod
    def from_json(cls, payload: dict[str, Any] | None) -> Self:
        data = payload or {}
        return cls(
            start_line=int(data.get("startLine", 0)),
            start_column=int(data.get("startColumn", 0)),
            end_line=int(data.get("endLine", 0)),
            end_column=int(data.get("endColumn", 0)),
        )


@dataclass(frozen=True, slots=True)
class SyntaxErrorInfo:
    message: str
    line: int = 0
    column: int = 0
    code: int = 0


@dataclass(frozen=True, slots=True)
class AnalyzedFile:
    path: str
    language: str
    bytes: int = 0
    lines: int = 0
    sha256: str = ""
    syntax_errors: tuple[SyntaxErrorInfo, ...] = ()


@dataclass(frozen=True, slots=True)
class SymbolRecord:
    """One definition. ``id`` is ``<file>#<qualifiedName>``, suffixed on redeclaration."""

    id: str
    name: str
    qualified_name: str
    kind: SymbolKind
    file: str
    range: SourceRange = field(default_factory=SourceRange)
    exported: bool = False
    is_async: bool = False
    is_generator: bool = False
    is_static: bool = False
    is_abstract: bool = False
    parent_id: str | None = None
    signature: str | None = None
    doc_summary: str | None = None


@dataclass(frozen=True, slots=True)
class ImportBinding:
    imported: str
    local: str
    kind: BindingKind = BindingKind.NAMED


@dataclass(frozen=True, slots=True)
class ImportRecord:
    file: str
    module_specifier: str
    kind: ImportKind = ImportKind.STATIC
    type_only: bool = False
    resolution: ImportResolution = ImportResolution.UNRESOLVED
    resolved_file: str | None = None
    unresolved_reason: str | None = None
    bindings: tuple[ImportBinding, ...] = ()
    range: SourceRange = field(default_factory=SourceRange)


@dataclass(frozen=True, slots=True)
class DependencyEdge:
    """File-to-file, or file-to-package for anything outside the analysis set."""

    from_: str
    to: str
    scope: str = "internal"
    count: int = 1


@dataclass(frozen=True, slots=True)
class CallEdge:
    """One call site. ``to`` is set only when ``resolution`` is ``resolved``."""

    id: str
    from_file: str
    callee_text: str
    resolution: CallResolution
    confidence: Confidence
    reason: str
    from_: str | None = None
    to: str | None = None
    resolved_file: str | None = None
    callee_name: str | None = None
    call_kind: str = "function"
    external_module: str | None = None
    range: SourceRange = field(default_factory=SourceRange)

    @property
    def score(self) -> Decimal:
        """The numeric confidence the ``edges`` table stores."""
        return CONFIDENCE_SCORES[self.confidence]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: DiagnosticSeverity
    code: str
    message: str
    path: str | None = None


@dataclass(frozen=True, slots=True)
class LimitReport:
    """Whether the analysis is complete, and if not, which budget stopped it."""

    applied: dict[str, int] = field(default_factory=dict)
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolInfo:
    name: str
    version: str = ""
    runtime: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisDocument:
    """Everything one analysis run produced."""

    tool: ToolInfo
    root: str = ""
    schema_version: str = SCHEMA_VERSION
    files: tuple[AnalyzedFile, ...] = ()
    symbols: tuple[SymbolRecord, ...] = ()
    imports: tuple[ImportRecord, ...] = ()
    dependencies: tuple[DependencyEdge, ...] = ()
    call_edges: tuple[CallEdge, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()
    limits: LimitReport = field(default_factory=LimitReport)

    def symbol_by_id(self, identifier: str) -> SymbolRecord | None:
        return next((item for item in self.symbols if item.id == identifier), None)


def parse_analysis_document(payload: dict[str, Any]) -> AnalysisDocument:
    """Read the JSON an external analyser produced into the shared shape.

    Unknown fields are ignored and missing ones fall back to their defaults, so a newer
    analyser that adds a field does not break an older caller.
    """
    tool = payload.get("tool") or {}
    return AnalysisDocument(
        tool=ToolInfo(
            name=str(tool.get("name", "")),
            version=str(tool.get("version", "")),
            runtime=str(tool.get("typescript") or tool.get("runtime") or ""),
        ),
        root=str(payload.get("root", "")),
        schema_version=str(payload.get("schemaVersion", SCHEMA_VERSION)),
        files=tuple(_file(item) for item in payload.get("files", [])),
        symbols=tuple(_symbol(item) for item in payload.get("symbols", [])),
        imports=tuple(_import(item) for item in payload.get("imports", [])),
        dependencies=tuple(_dependency(item) for item in payload.get("dependencies", [])),
        call_edges=tuple(_call_edge(item) for item in payload.get("callEdges", [])),
        diagnostics=tuple(_diagnostic(item) for item in payload.get("diagnostics", [])),
        limits=_limits(payload.get("limits") or {}),
    )


def _file(payload: dict[str, Any]) -> AnalyzedFile:
    return AnalyzedFile(
        path=str(payload.get("path", "")),
        language=str(payload.get("language", "")),
        bytes=int(payload.get("bytes", 0)),
        lines=int(payload.get("lines", 0)),
        sha256=str(payload.get("sha256", "")),
        syntax_errors=tuple(
            SyntaxErrorInfo(
                message=str(item.get("message", "")),
                line=int(item.get("line", 0)),
                column=int(item.get("column", 0)),
                code=int(item.get("code", 0)),
            )
            for item in payload.get("syntaxErrors", [])
        ),
    )


def _symbol(payload: dict[str, Any]) -> SymbolRecord:
    return SymbolRecord(
        id=str(payload.get("id", "")),
        name=str(payload.get("name", "")),
        qualified_name=str(payload.get("qualifiedName", "")),
        kind=_enum(SymbolKind, payload.get("kind"), SymbolKind.VARIABLE),
        file=str(payload.get("file", "")),
        range=SourceRange.from_json(payload.get("range")),
        exported=bool(payload.get("exported", False)),
        is_async=bool(payload.get("isAsync", False)),
        is_generator=bool(payload.get("isGenerator", False)),
        is_static=bool(payload.get("isStatic", False)),
        is_abstract=bool(payload.get("isAbstract", False)),
        parent_id=payload.get("parentId"),
        signature=payload.get("signature"),
        doc_summary=payload.get("docSummary"),
    )


def _import(payload: dict[str, Any]) -> ImportRecord:
    return ImportRecord(
        file=str(payload.get("file", "")),
        module_specifier=str(payload.get("moduleSpecifier", "")),
        kind=_enum(ImportKind, payload.get("kind"), ImportKind.STATIC),
        type_only=bool(payload.get("typeOnly", False)),
        resolution=_enum(
            ImportResolution, payload.get("resolution"), ImportResolution.UNRESOLVED
        ),
        resolved_file=payload.get("resolvedFile"),
        unresolved_reason=payload.get("unresolvedReason"),
        bindings=tuple(
            ImportBinding(
                imported=str(item.get("imported", "")),
                local=str(item.get("local", "")),
                kind=_enum(BindingKind, item.get("kind"), BindingKind.NAMED),
            )
            for item in payload.get("bindings", [])
        ),
        range=SourceRange.from_json(payload.get("range")),
    )


def _dependency(payload: dict[str, Any]) -> DependencyEdge:
    return DependencyEdge(
        from_=str(payload.get("from", "")),
        to=str(payload.get("to", "")),
        scope=str(payload.get("scope", "internal")),
        count=int(payload.get("count", 1)),
    )


def _call_edge(payload: dict[str, Any]) -> CallEdge:
    return CallEdge(
        id=str(payload.get("id", "")),
        from_=payload.get("from"),
        from_file=str(payload.get("fromFile", "")),
        to=payload.get("to"),
        resolved_file=payload.get("resolvedFile"),
        callee_text=str(payload.get("calleeText", "")),
        callee_name=payload.get("calleeName"),
        resolution=_enum(CallResolution, payload.get("resolution"), CallResolution.UNRESOLVED),
        confidence=_enum(Confidence, payload.get("confidence"), Confidence.LOW),
        reason=str(payload.get("reason", "")),
        call_kind=str(payload.get("callKind", "unknown")),
        external_module=payload.get("externalModule"),
        range=SourceRange.from_json(payload.get("range")),
    )


def _diagnostic(payload: dict[str, Any]) -> Diagnostic:
    return Diagnostic(
        severity=_enum(DiagnosticSeverity, payload.get("severity"), DiagnosticSeverity.INFO),
        code=str(payload.get("code", "")),
        message=str(payload.get("message", "")),
        path=payload.get("path"),
    )


def _limits(payload: dict[str, Any]) -> LimitReport:
    applied = payload.get("applied") or {}
    return LimitReport(
        applied={str(key): int(value) for key, value in applied.items()},
        truncated=bool(payload.get("truncated", False)),
        truncation_reasons=tuple(str(item) for item in payload.get("truncationReasons", [])),
    )


def _enum[T: StrEnum](enum: type[T], value: Any, fallback: T) -> T:
    try:
        return enum(str(value))
    except ValueError:
        return fallback
