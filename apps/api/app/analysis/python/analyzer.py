"""Reading Python source without running it.

``ast`` supplies the structure — what is defined, where, and what is called. ``symtable``
supplies the part the syntax tree cannot: whether the name at a call site is a parameter, a
local, an import or a module-level definition. Without that, ``handler()`` inside
``def run(handler)`` looks exactly like a call to a module-level ``handler``, and reporting
it as one would invent a relationship that does not exist.

That is the rule this module is built around. Python resolves almost everything at runtime,
so a plausible guess is nearly always available; every one of them is refused. An edge is
``resolved`` only when the target is a definition that also appears in ``symbols``, and
anything else carries its real resolution, low confidence and a reason saying why.
"""

from __future__ import annotations

import ast
import builtins
import hashlib
import symtable
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum, auto
from typing import Final

from app.analysis.model import (
    SCHEMA_VERSION,
    AnalysisDocument,
    AnalyzedFile,
    BindingKind,
    CallEdge,
    CallResolution,
    Confidence,
    DependencyEdge,
    Diagnostic,
    DiagnosticSeverity,
    ImportBinding,
    ImportKind,
    ImportRecord,
    ImportResolution,
    LimitReport,
    SourceRange,
    SymbolKind,
    SymbolRecord,
    SyntaxErrorInfo,
    ToolInfo,
)

TOOL_NAME: Final = "quick-study.analysis.python"
LANGUAGE: Final = "python"

#: Python's own qualified-name marker for anything defined inside a function body.
LOCALS_MARKER: Final = "<locals>"

_BUILTIN_NAMES: Final[frozenset[str]] = frozenset(dir(builtins))

#: Callables whose result is decided at runtime. A call through one is never resolved.
DYNAMIC_CALLABLES: Final[frozenset[str]] = frozenset(
    {"getattr", "eval", "exec", "__import__", "globals", "locals", "vars", "compile"}
)


class Reason(StrEnum):
    """The closed vocabulary of justifications a call edge may carry."""

    MODULE_LEVEL_DEFINITION = "module-level-definition"
    IMPORT_OF_ANALYSED_DEFINITION = "import-of-analysed-definition"
    IMPORTED_MODULE_ATTRIBUTE = "imported-module-attribute"
    IMPORTED_CLASS_MEMBER = "imported-class-member"
    IMPORTED_NAME_NOT_DEFINED = "imported-name-not-defined-in-target"
    EXTERNAL_MODULE = "external-module"
    BUILTIN = "builtin"
    SELF_METHOD = "self-method"
    SELF_ATTRIBUTE_NOT_DEFINED = "self-attribute-not-defined-in-class"
    LOCAL_BINDING = "local-binding"
    PARAMETER_INVOCATION = "parameter-invocation"
    ATTRIBUTE_ON_UNKNOWN_RECEIVER = "attribute-on-unknown-receiver"
    DYNAMIC_CALL_RESULT = "dynamic-call-result"
    COMPUTED_CALLEE = "computed-callee"
    NO_BINDING = "no-binding-for-callee"


class _BindingKind(StrEnum):
    PARAMETER = auto()
    LOCAL = auto()
    IMPORTED = auto()
    MODULE_LEVEL = auto()
    BUILTIN = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class PythonAnalysisLimits:
    """What one analysis run is allowed to read."""

    max_files: int = 2000
    max_file_bytes: int = 512 * 1024
    max_total_bytes: int = 48 * 1024 * 1024


def analyze_python(
    sources: Mapping[str, str],
    *,
    root: str = "",
    limits: PythonAnalysisLimits | None = None,
) -> AnalysisDocument:
    """Analyse a set of Python files, addressed by repository-relative path."""
    budget = limits or PythonAnalysisLimits()
    diagnostics: list[Diagnostic] = []
    accepted, truncation = _select(sources, budget, diagnostics)

    modules = _ModuleIndex(accepted)
    parsed: dict[str, ast.Module] = {}
    files: list[AnalyzedFile] = []

    for path in accepted:
        source = sources[path]
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as error:
            files.append(_file_record(path, source, error))
            diagnostics.append(
                Diagnostic(
                    DiagnosticSeverity.ERROR, "syntax-error", str(error.msg or error), path
                )
            )
            continue
        parsed[path] = tree
        files.append(_file_record(path, source))

    symbols: list[SymbolRecord] = []
    imports: list[ImportRecord] = []
    for path in parsed:
        collector = _SymbolCollector(path)
        collector.visit(parsed[path])
        symbols.extend(collector.symbols)
        imports.extend(_imports_of(path, parsed[path], modules))

    index = _SymbolIndex(symbols)
    bindings = {path: _bindings_of(record) for path, record in _group(imports).items()}

    call_edges: list[CallEdge] = []
    for path, tree in parsed.items():
        try:
            table = symtable.symtable(sources[path], path, "exec")
        except SyntaxError:  # pragma: no cover - already reported above
            continue
        walker = _CallCollector(
            path=path,
            table=table,
            index=index,
            modules=modules,
            imports=bindings.get(path, {}),
        )
        walker.visit(tree)
        call_edges.extend(walker.edges)

    return AnalysisDocument(
        tool=ToolInfo(name=TOOL_NAME, version=SCHEMA_VERSION, runtime="cpython"),
        root=root,
        schema_version=SCHEMA_VERSION,
        files=tuple(files),
        symbols=tuple(symbols),
        imports=tuple(imports),
        dependencies=_dependencies(imports),
        call_edges=tuple(call_edges),
        diagnostics=tuple(diagnostics),
        limits=LimitReport(
            applied={
                "maxFiles": budget.max_files,
                "maxFileBytes": budget.max_file_bytes,
                "maxTotalBytes": budget.max_total_bytes,
            },
            truncated=bool(truncation),
            truncation_reasons=tuple(truncation),
        ),
    )


def _select(
    sources: Mapping[str, str],
    budget: PythonAnalysisLimits,
    diagnostics: list[Diagnostic],
) -> tuple[list[str], list[str]]:
    """Choose which files to read, in a deterministic order, inside the budget."""
    accepted: list[str] = []
    reasons: list[str] = []
    total = 0
    for path in sorted(sources):
        if len(accepted) >= budget.max_files:
            reasons.append("limit.max-files")
            break
        size = len(sources[path].encode("utf-8"))
        if size > budget.max_file_bytes:
            diagnostics.append(
                Diagnostic(
                    DiagnosticSeverity.WARNING,
                    "file-too-large",
                    f"{size} bytes exceeds the {budget.max_file_bytes} byte budget",
                    path,
                )
            )
            reasons.append("limit.max-file-bytes")
            continue
        if total + size > budget.max_total_bytes:
            reasons.append("limit.max-total-bytes")
            break
        total += size
        accepted.append(path)
    return accepted, list(dict.fromkeys(reasons))


def _file_record(path: str, source: str, error: SyntaxError | None = None) -> AnalyzedFile:
    return AnalyzedFile(
        path=path,
        language=LANGUAGE,
        bytes=len(source.encode("utf-8")),
        lines=source.count("\n") + 1,
        sha256=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        syntax_errors=()
        if error is None
        else (
            SyntaxErrorInfo(
                message=str(error.msg or error), line=error.lineno or 0, column=error.offset or 0
            ),
        ),
    )


class _ModuleIndex:
    """Maps dotted module names onto the files in the analysis set.

    Every suffix of a file's dotted path is registered, because a repository may keep its
    package under ``src/`` while importing it as ``gateway.config``. A suffix that several
    files could answer to is deliberately left unresolved rather than guessed at.
    """

    def __init__(self, paths: list[str]) -> None:
        self._by_name: dict[str, list[str]] = {}
        self._module_of: dict[str, str] = {}
        for path in paths:
            parts = _module_parts(path)
            if not parts:
                continue
            self._module_of[path] = ".".join(parts)
            for start in range(len(parts)):
                self._by_name.setdefault(".".join(parts[start:]), []).append(path)

    def resolve(self, module: str) -> str | None:
        candidates = self._by_name.get(module, ())
        return candidates[0] if len(candidates) == 1 else None

    def package_of(self, path: str) -> str:
        """The dotted package a relative import inside ``path`` is measured from."""
        parts = _module_parts(path)
        return ".".join(parts[:-1]) if not path.endswith("__init__.py") else ".".join(parts)


def _module_parts(path: str) -> list[str]:
    stem = path[:-3] if path.endswith(".py") else path
    parts = [segment for segment in stem.split("/") if segment]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return parts


class _SymbolCollector(ast.NodeVisitor):
    """Walks one module, recording definitions with Python's own qualified names."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._qualifiers: list[str] = []
        self._parents: list[str] = []
        self._used_ids: dict[str, int] = {}
        self._exports: frozenset[str] | None = None
        self.symbols: list[SymbolRecord] = []

    def visit_Module(self, node: ast.Module) -> None:
        self._exports = _dunder_all(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        record = self._record(node, SymbolKind.CLASS)
        self._descend(node, record, qualifier=node.name)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id != "__all__":
                self._variable(node, target.id)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            self._variable(node, node.target.id)

    def visit_TypeAlias(self, node: ast.TypeAlias) -> None:
        if isinstance(node.name, ast.Name):
            self._record(node, SymbolKind.TYPE_ALIAS, name=node.name.id)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        inside_class = bool(self._parents) and self._parents[-1] == "class"
        kind = SymbolKind.METHOD if inside_class else SymbolKind.FUNCTION
        record = self._record(
            node,
            kind,
            signature=_signature(node),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            is_generator=_is_generator(node),
        )
        self._descend(node, record, qualifier=f"{node.name}.{LOCALS_MARKER}")

    def _variable(self, node: ast.Assign | ast.AnnAssign, name: str) -> None:
        self._record(node, SymbolKind.VARIABLE, name=name)

    def _descend(self, node: ast.AST, record: SymbolRecord, *, qualifier: str) -> None:
        self._qualifiers.append(qualifier)
        self._parents.append("class" if record.kind is SymbolKind.CLASS else "function")
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._parents.pop()
        self._qualifiers.pop()

    def _record(
        self,
        node: ast.AST,
        kind: SymbolKind,
        *,
        name: str | None = None,
        signature: str | None = None,
        is_async: bool = False,
        is_generator: bool = False,
    ) -> SymbolRecord:
        label = name if name is not None else getattr(node, "name", "")
        qualified = ".".join([*self._qualifiers, label])
        record = SymbolRecord(
            id=self._allocate(qualified),
            name=label,
            qualified_name=qualified,
            kind=kind,
            file=self._path,
            range=_range(node),
            exported=self._is_exported(label),
            is_async=is_async,
            is_generator=is_generator,
            parent_id=self._parent_id(),
            signature=signature,
            doc_summary=_doc_summary(node),
        )
        self.symbols.append(record)
        return record

    def _allocate(self, qualified: str) -> str:
        """Give a redeclared name a distinct identifier instead of merging the two."""
        count = self._used_ids.get(qualified, 0) + 1
        self._used_ids[qualified] = count
        suffix = "" if count == 1 else f"~{count}"
        return f"{self._path}#{qualified}{suffix}"

    def _parent_id(self) -> str | None:
        if not self._qualifiers:
            return None
        parent = ".".join(self._qualifiers).removesuffix(f".{LOCALS_MARKER}")
        return f"{self._path}#{parent}"

    def _is_exported(self, name: str) -> bool:
        if self._qualifiers:
            return not name.startswith("_")
        if self._exports is not None:
            return name in self._exports
        return not name.startswith("_")


def _dunder_all(module: ast.Module) -> frozenset[str] | None:
    for node in module.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                return frozenset(
                    element.value
                    for element in getattr(node.value, "elts", [])
                    if isinstance(element, ast.Constant) and isinstance(element.value, str)
                )
    return None


def _imports_of(path: str, tree: ast.Module, modules: _ModuleIndex) -> list[ImportRecord]:
    records: list[ImportRecord] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            records.extend(_plain_imports(path, node, modules))
        elif isinstance(node, ast.ImportFrom):
            records.append(_from_import(path, node, modules))
    return records


def _plain_imports(path: str, node: ast.Import, modules: _ModuleIndex) -> list[ImportRecord]:
    records = []
    for alias in node.names:
        local = alias.asname or alias.name.split(".")[0]
        records.append(
            _resolved_import(
                path=path,
                module=alias.name,
                bindings=(ImportBinding("*", local, BindingKind.NAMESPACE),),
                node=node,
                modules=modules,
            )
        )
    return records


def _from_import(path: str, node: ast.ImportFrom, modules: _ModuleIndex) -> ImportRecord:
    module = _absolute_module(path, node, modules)
    bindings = tuple(
        ImportBinding(alias.name, alias.asname or alias.name, BindingKind.NAMED)
        for alias in node.names
    )
    return _resolved_import(
        path=path, module=module, bindings=bindings, node=node, modules=modules
    )


def _absolute_module(path: str, node: ast.ImportFrom, modules: _ModuleIndex) -> str:
    """Turn ``from ..pkg import x`` into the dotted module it actually names."""
    if not node.level:
        return node.module or ""
    package = modules.package_of(path).split(".")
    package = [segment for segment in package if segment]
    ascend = node.level - 1
    base = package[: len(package) - ascend] if ascend else package
    return ".".join([*base, node.module]) if node.module else ".".join(base)


def _resolved_import(
    *,
    path: str,
    module: str,
    bindings: tuple[ImportBinding, ...],
    node: ast.AST,
    modules: _ModuleIndex,
) -> ImportRecord:
    resolved = modules.resolve(module) if module else None
    return ImportRecord(
        file=path,
        module_specifier=module,
        kind=ImportKind.STATIC,
        resolution=ImportResolution.INTERNAL if resolved else ImportResolution.EXTERNAL,
        resolved_file=resolved,
        unresolved_reason=None if resolved else "module-outside-analysis-set",
        bindings=bindings,
        range=_range(node),
    )


def _group(imports: list[ImportRecord]) -> dict[str, list[ImportRecord]]:
    grouped: dict[str, list[ImportRecord]] = {}
    for record in imports:
        grouped.setdefault(record.file, []).append(record)
    return grouped


@dataclass(frozen=True, slots=True)
class _LocalImport:
    """What a local name introduced by an import actually refers to."""

    module: str
    imported: str | None
    resolved_file: str | None

    @property
    def is_module_alias(self) -> bool:
        return self.imported is None

    @property
    def package(self) -> str:
        return self.module.split(".")[0]


def _bindings_of(records: list[ImportRecord]) -> dict[str, _LocalImport]:
    table: dict[str, _LocalImport] = {}
    for record in records:
        for binding in record.bindings:
            imported = None if binding.kind is BindingKind.NAMESPACE else binding.imported
            table[binding.local] = _LocalImport(
                module=record.module_specifier,
                imported=imported,
                resolved_file=record.resolved_file,
            )
    return table


def _dependencies(imports: list[ImportRecord]) -> tuple[DependencyEdge, ...]:
    counts: dict[tuple[str, str, str], int] = {}
    for record in imports:
        if record.resolution is ImportResolution.INTERNAL and record.resolved_file:
            key = (record.file, record.resolved_file, "internal")
        else:
            package = record.module_specifier.split(".")[0]
            if not package:
                continue
            key = (record.file, package, "external")
        counts[key] = counts.get(key, 0) + 1
    return tuple(
        DependencyEdge(from_=source, to=target, scope=scope, count=count)
        for (source, target, scope), count in sorted(counts.items())
    )


class _SymbolIndex:
    """Looks symbols up the way a call site needs to: by file and qualified name."""

    def __init__(self, symbols: list[SymbolRecord]) -> None:
        self._by_key: dict[tuple[str, str], SymbolRecord] = {}
        for record in symbols:
            self._by_key.setdefault((record.file, record.qualified_name), record)

    def find(self, file: str, qualified_name: str) -> SymbolRecord | None:
        return self._by_key.get((file, qualified_name))


@dataclass(slots=True)
class _Scope:
    """One lexical scope, paired with the symtable that describes its bindings."""

    table: symtable.SymbolTable
    kind: str
    symbol_id: str | None = None
    class_qualname: str | None = None
    self_name: str | None = None


class _CallCollector(ast.NodeVisitor):
    """Walks one module recording every call, and how firmly its target is known."""

    def __init__(
        self,
        *,
        path: str,
        table: symtable.SymbolTable,
        index: _SymbolIndex,
        modules: _ModuleIndex,
        imports: dict[str, _LocalImport],
    ) -> None:
        self._path = path
        self._index = index
        self._modules = modules
        self._imports = imports
        self._scopes = [_Scope(table=table, kind="module")]
        self._children = [_children_of(table)]
        self._qualifiers: list[str] = []
        self.edges: list[CallEdge] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualified = ".".join([*self._qualifiers, node.name])
        self._enter(
            node.name,
            "class",
            qualifier=node.name,
            symbol_id=f"{self._path}#{qualified}",
            class_qualname=qualified,
        )
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._leave()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node)

    def visit_Call(self, node: ast.Call) -> None:
        self.edges.append(self._describe(node))
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        qualified = ".".join([*self._qualifiers, node.name])
        enclosing = self._scopes[-1]
        receiver = _first_parameter(node) if enclosing.kind == "class" else None
        self._enter(
            node.name,
            "function",
            qualifier=f"{node.name}.{LOCALS_MARKER}",
            symbol_id=f"{self._path}#{qualified}",
            class_qualname=enclosing.class_qualname,
            self_name=receiver,
        )
        for child in ast.iter_child_nodes(node):
            self.visit(child)
        self._leave()

    def _enter(
        self,
        name: str,
        kind: str,
        *,
        qualifier: str,
        symbol_id: str,
        class_qualname: str | None = None,
        self_name: str | None = None,
    ) -> None:
        table = self._next_child(name)
        self._scopes.append(
            _Scope(
                table=table or self._scopes[-1].table,
                kind=kind,
                symbol_id=symbol_id,
                class_qualname=class_qualname,
                self_name=self_name,
            )
        )
        self._children.append(_children_of(table) if table else {})
        self._qualifiers.append(qualifier)

    def _leave(self) -> None:
        self._scopes.pop()
        self._children.pop()
        self._qualifiers.pop()

    def _next_child(self, name: str) -> symtable.SymbolTable | None:
        pending = self._children[-1].get(name)
        return pending.popleft() if pending else None

    @property
    def _enclosing_symbol(self) -> str | None:
        for scope in reversed(self._scopes):
            if scope.kind == "function":
                return scope.symbol_id
        return None

    def _describe(self, node: ast.Call) -> CallEdge:
        callee = node.func
        if isinstance(callee, ast.Name):
            outcome = self._from_name(callee.id)
        elif isinstance(callee, ast.Attribute):
            outcome = self._from_attribute(callee)
        elif isinstance(callee, ast.Call):
            outcome = _unresolved(Reason.DYNAMIC_CALL_RESULT, call_kind="unknown")
        else:
            outcome = _unresolved(Reason.COMPUTED_CALLEE, call_kind="unknown")
        return CallEdge(
            id=f"{self._path}:{node.lineno}:{node.col_offset}",
            from_=self._enclosing_symbol,
            from_file=self._path,
            to=outcome.target.id if outcome.target else None,
            resolved_file=outcome.target.file if outcome.target else None,
            callee_text=ast.unparse(callee),
            callee_name=_callee_name(callee),
            resolution=outcome.resolution,
            confidence=outcome.confidence,
            reason=outcome.reason.value,
            call_kind=outcome.call_kind,
            external_module=outcome.external_module,
            range=_range(node),
        )

    def _from_name(self, name: str) -> _Outcome:
        binding = self._classify(name)
        if binding is _BindingKind.PARAMETER:
            return _unresolved(Reason.PARAMETER_INVOCATION)
        if binding is _BindingKind.LOCAL:
            return _unresolved(Reason.LOCAL_BINDING)
        if binding is _BindingKind.IMPORTED:
            return self._through_import(name)
        if binding is _BindingKind.MODULE_LEVEL:
            target = self._index.find(self._path, name)
            if target is not None:
                return _resolved(target, Reason.MODULE_LEVEL_DEFINITION, Confidence.HIGH)
            return _unresolved(Reason.NO_BINDING)
        if binding is _BindingKind.BUILTIN:
            return _Outcome(
                resolution=CallResolution.EXTERNAL,
                confidence=Confidence.MEDIUM,
                reason=Reason.BUILTIN,
                external_module="builtins",
            )
        return _unresolved(Reason.NO_BINDING)

    def _from_attribute(self, callee: ast.Attribute) -> _Outcome:
        receiver = callee.value
        if not isinstance(receiver, ast.Name):
            return _unresolved(Reason.ATTRIBUTE_ON_UNKNOWN_RECEIVER, call_kind="method")

        scope = self._scopes[-1]
        if scope.self_name and receiver.id == scope.self_name and scope.class_qualname:
            qualified = f"{scope.class_qualname}.{callee.attr}"
            target = self._index.find(self._path, qualified)
            if target is not None:
                # A subclass may override this, so the target is likely, not certain.
                return _resolved(target, Reason.SELF_METHOD, Confidence.MEDIUM, "method")
            return _unresolved(Reason.SELF_ATTRIBUTE_NOT_DEFINED, call_kind="method")

        binding = self._classify(receiver.id)
        if binding is not _BindingKind.IMPORTED:
            return _unresolved(Reason.ATTRIBUTE_ON_UNKNOWN_RECEIVER, call_kind="method")
        return self._through_import(receiver.id, attribute=callee.attr)

    def _through_import(self, local: str, attribute: str | None = None) -> _Outcome:
        record = self._imports.get(local)
        if record is None:
            return _unresolved(Reason.NO_BINDING)

        if record.resolved_file is None:
            return _Outcome(
                resolution=CallResolution.EXTERNAL,
                confidence=Confidence.MEDIUM,
                reason=Reason.EXTERNAL_MODULE,
                external_module=record.package,
                call_kind="method" if attribute else "function",
            )

        qualified = _imported_qualified_name(record, attribute)
        if qualified is None:
            return _unresolved(Reason.IMPORTED_NAME_NOT_DEFINED)
        target = self._index.find(record.resolved_file, qualified)
        if target is None:
            return _unresolved(Reason.IMPORTED_NAME_NOT_DEFINED)
        reason = (
            Reason.IMPORTED_MODULE_ATTRIBUTE
            if record.is_module_alias
            else Reason.IMPORTED_CLASS_MEMBER
            if attribute
            else Reason.IMPORT_OF_ANALYSED_DEFINITION
        )
        confidence = Confidence.MEDIUM if attribute and not record.is_module_alias else (
            Confidence.HIGH
        )
        return _resolved(target, reason, confidence)

    def _classify(self, name: str) -> _BindingKind:
        """Ask symtable what this name is, walking outwards as Python would.

        Class scopes are skipped on the way out because a function body does not see the
        attributes of a class it is defined in.
        """
        for depth, scope in reversed(list(enumerate(self._scopes))):
            if depth and scope.kind == "class":
                continue
            try:
                found = scope.table.lookup(name)
            except KeyError:
                continue
            if found.is_imported():
                return _BindingKind.IMPORTED
            if found.is_parameter():
                return _BindingKind.PARAMETER
            if found.is_local():
                return (
                    _BindingKind.MODULE_LEVEL if scope.kind == "module" else _BindingKind.LOCAL
                )
        return _BindingKind.BUILTIN if name in _BUILTIN_NAMES else _BindingKind.UNKNOWN


def _imported_qualified_name(record: _LocalImport, attribute: str | None) -> str | None:
    if record.is_module_alias:
        return attribute
    if attribute is None:
        return record.imported
    return f"{record.imported}.{attribute}"


@dataclass(frozen=True, slots=True)
class _Outcome:
    resolution: CallResolution
    confidence: Confidence
    reason: Reason
    target: SymbolRecord | None = None
    external_module: str | None = None
    call_kind: str = "function"


def _resolved(
    target: SymbolRecord,
    reason: Reason,
    confidence: Confidence,
    call_kind: str | None = None,
) -> _Outcome:
    kind = call_kind or ("constructor" if target.kind is SymbolKind.CLASS else "function")
    return _Outcome(
        resolution=CallResolution.RESOLVED,
        confidence=confidence,
        reason=reason,
        target=target,
        call_kind=kind,
    )


def _unresolved(reason: Reason, *, call_kind: str = "function") -> _Outcome:
    return _Outcome(
        resolution=CallResolution.UNRESOLVED,
        confidence=Confidence.LOW,
        reason=reason,
        call_kind=call_kind,
    )


def _children_of(table: symtable.SymbolTable) -> dict[str, deque[symtable.SymbolTable]]:
    """Index a scope's child scopes by name, in source order."""
    children: dict[str, deque[symtable.SymbolTable]] = {}
    for child in table.get_children():
        children.setdefault(child.get_name(), deque()).append(child)
    return children


def _first_parameter(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str | None:
    positional = [*node.args.posonlyargs, *node.args.args]
    return positional[0].arg if positional else None


def _callee_name(callee: ast.expr) -> str | None:
    if isinstance(callee, ast.Name):
        return callee.id
    if isinstance(callee, ast.Attribute):
        return callee.attr
    return None


def _range(node: ast.AST) -> SourceRange:
    return SourceRange(
        start_line=getattr(node, "lineno", 0),
        start_column=getattr(node, "col_offset", 0),
        end_line=getattr(node, "end_lineno", 0) or getattr(node, "lineno", 0),
        end_column=getattr(node, "end_col_offset", 0) or 0,
    )


def _doc_summary(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
        return None
    docstring = ast.get_docstring(node, clean=True)
    return docstring.strip().splitlines()[0] if docstring else None


def _is_generator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(child, ast.Yield | ast.YieldFrom)
        for child in ast.walk(node)
        if not isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef) or child is node
    )


def _signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """Render a signature the way a reader expects to see it, not the way ``ast`` does.

    ``ast.unparse`` writes ``int=128`` for an annotated default; these signatures end up
    in generated documentation, so they follow PEP 8 spacing instead.
    """
    rendered = _format_arguments(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"({rendered}){returns}"


def _format_arguments(args: ast.arguments) -> str:
    parts: list[str] = []
    positional = [*args.posonlyargs, *args.args]
    defaults = list(args.defaults)
    offset = len(positional) - len(defaults)
    for index, argument in enumerate(positional):
        default = defaults[index - offset] if index >= offset else None
        parts.append(_format_argument(argument, default))
        if args.posonlyargs and index == len(args.posonlyargs) - 1:
            parts.append("/")
    if args.vararg is not None:
        parts.append(f"*{_format_argument(args.vararg, None)}")
    elif args.kwonlyargs:
        parts.append("*")
    for argument, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        parts.append(_format_argument(argument, default))
    if args.kwarg is not None:
        parts.append(f"**{_format_argument(args.kwarg, None)}")
    return ", ".join(parts)


def _format_argument(argument: ast.arg, default: ast.expr | None) -> str:
    text = argument.arg
    if argument.annotation is not None:
        text += f": {ast.unparse(argument.annotation)}"
    if default is not None:
        separator = " = " if argument.annotation is not None else "="
        text += f"{separator}{ast.unparse(default)}"
    return text
