"""The Python side of the JavaScript/TypeScript analyser.

The analyser itself lives in ``packages/ts-analyzer`` and is spawned as a subprocess. What
is tested here is the contract between the two: the command is fully injected, so nothing
from the repository under analysis can influence what gets executed; the JSON on stdout
becomes the same document shape the Python analyser produces; and every documented failure
mode turns into a distinct, catchable error.

The tests drive a stub program rather than Node, so the whole exchange — argument vector,
exit codes, stdout, stderr, timeouts — is exercised offline.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from app.analysis.model import CallResolution, Confidence, ImportResolution, SymbolKind
from app.analysis.typescript import (
    DEFAULT_COMMAND,
    AnalyzerError,
    AnalyzerTimeout,
    AnalyzerUnavailable,
    TypeScriptAnalyzer,
    TypeScriptAnalyzerLimits,
)

DOCUMENT = {
    "schemaVersion": "1.0.0",
    "tool": {"name": "@quick-study/ts-analyzer", "version": "0.1.0", "typescript": "5.9.3"},
    "root": "/repo",
    "files": [
        {
            "path": "src/service.ts",
            "language": "ts",
            "bytes": 120,
            "lines": 8,
            "sha256": "a" * 64,
            "syntaxErrors": [],
        }
    ],
    "symbols": [
        {
            "id": "src/service.ts#Repository.load",
            "name": "load",
            "qualifiedName": "Repository.load",
            "kind": "method",
            "file": "src/service.ts",
            "range": {"startLine": 4, "startColumn": 2, "endLine": 6, "endColumn": 3},
            "exported": True,
            "exportKind": "named",
            "isAsync": True,
            "parentId": "src/service.ts#Repository",
            "signature": "(id: string): Promise<Row>",
            "docSummary": "Load one row.",
        }
    ],
    "imports": [
        {
            "file": "src/service.ts",
            "moduleSpecifier": "./db",
            "kind": "static",
            "typeOnly": False,
            "resolution": "internal",
            "resolvedFile": "src/db.ts",
            "bindings": [{"imported": "query", "local": "query", "kind": "named"}],
            "range": {"startLine": 1, "startColumn": 0, "endLine": 1, "endColumn": 30},
        }
    ],
    "dependencies": [
        {"from": "src/service.ts", "to": "src/db.ts", "scope": "internal", "count": 1}
    ],
    "callEdges": [
        {
            "id": "edge-1",
            "from": "src/service.ts#Repository.load",
            "fromFile": "src/service.ts",
            "to": "src/db.ts#query",
            "calleeText": "query",
            "calleeName": "query",
            "resolution": "resolved",
            "confidence": "high",
            "reason": "checker-unique-declaration",
            "callKind": "function",
            "range": {"startLine": 5, "startColumn": 4, "endLine": 5, "endColumn": 20},
        },
        {
            "id": "edge-2",
            "from": None,
            "fromFile": "src/service.ts",
            "to": None,
            "calleeText": "obj[key]",
            "calleeName": None,
            "resolution": "unresolved",
            "confidence": "low",
            "reason": "computed-member-access",
            "callKind": "computed",
            "range": {"startLine": 7, "startColumn": 0, "endLine": 7, "endColumn": 12},
        },
    ],
    "diagnostics": [
        {"severity": "warning", "code": "symlink", "message": "skipped", "path": "src/link.ts"}
    ],
    "limits": {"applied": {"maxFiles": 2000}, "truncated": False, "truncationReasons": []},
}


def stub_program(tmp_path: Path, body: str) -> tuple[str, ...]:
    script = tmp_path / "stub_analyzer.py"
    script.write_text(body, encoding="utf-8")
    return (sys.executable, str(script))


def emitting(document: dict) -> str:
    return (
        "import json, sys\n"
        f"sys.stdout.write(json.dumps({document!r}))\n"
        "sys.exit(0)\n"
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "service.ts").write_text("export const a = 1;\n", encoding="utf-8")
    return root


class TestCommand:
    def test_the_default_command_names_the_built_cli_and_nothing_else(self) -> None:
        assert DEFAULT_COMMAND[0] == "node"
        assert DEFAULT_COMMAND[1].endswith("dist/cli.js")

    def test_the_root_and_every_entry_are_passed_as_arguments(self, repo: Path) -> None:
        analyzer = TypeScriptAnalyzer(command=("node", "cli.js"))

        command = analyzer.build_command(repo, ("src", "src/service.ts"))

        assert command[:2] == ("node", "cli.js")
        assert "--root" in command
        assert str(repo) in command
        assert "--dir=src" in command
        assert "--file=src/service.ts" in command

    def test_the_limits_are_passed_so_the_subprocess_bounds_itself(self, repo: Path) -> None:
        analyzer = TypeScriptAnalyzer(
            command=("node", "cli.js"),
            limits=TypeScriptAnalyzerLimits(max_files=50, time_budget_seconds=5.0),
        )

        command = analyzer.build_command(repo, ())

        assert "--max-files=50" in command
        assert "--time-budget-ms=5000" in command

    def test_strict_limits_are_requested_only_when_asked_for(self, repo: Path) -> None:
        analyzer = TypeScriptAnalyzer(command=("node", "cli.js"))

        assert "--strict-limits" not in analyzer.build_command(repo, ())
        assert "--strict-limits" in analyzer.build_command(repo, (), strict_limits=True)

    @pytest.mark.parametrize("entry", ["../outside", "/etc/passwd", "src/../../escape", ""])
    def test_an_entry_that_leaves_the_root_is_refused_before_anything_is_spawned(
        self, repo: Path, entry: str
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=("node", "cli.js"))

        with pytest.raises(AnalyzerError):
            analyzer.build_command(repo, (entry,))


class TestSuccessfulAnalysis:
    def test_the_json_on_stdout_becomes_the_shared_document(
        self, repo: Path, tmp_path: Path
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=stub_program(tmp_path, emitting(DOCUMENT)))

        result = analyzer.analyze(repo)

        assert result.tool.name == "@quick-study/ts-analyzer"
        assert [item.path for item in result.files] == ["src/service.ts"]
        assert result.symbols[0].kind is SymbolKind.METHOD
        assert result.symbols[0].is_async
        assert result.symbols[0].parent_id == "src/service.ts#Repository"

    def test_import_resolution_survives_the_crossing(
        self, repo: Path, tmp_path: Path
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=stub_program(tmp_path, emitting(DOCUMENT)))

        record = analyzer.analyze(repo).imports[0]

        assert record.resolution is ImportResolution.INTERNAL
        assert record.resolved_file == "src/db.ts"
        assert record.bindings[0].local == "query"

    def test_call_edges_keep_their_resolution_confidence_and_reason(
        self, repo: Path, tmp_path: Path
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=stub_program(tmp_path, emitting(DOCUMENT)))

        resolved, unresolved = analyzer.analyze(repo).call_edges

        assert resolved.resolution is CallResolution.RESOLVED
        assert resolved.confidence is Confidence.HIGH
        assert resolved.to == "src/db.ts#query"
        assert unresolved.resolution is CallResolution.UNRESOLVED
        assert unresolved.confidence is Confidence.LOW
        assert unresolved.to is None
        assert unresolved.reason == "computed-member-access"

    def test_the_confidence_maps_onto_the_score_the_database_stores(
        self, repo: Path, tmp_path: Path
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=stub_program(tmp_path, emitting(DOCUMENT)))

        edges = analyzer.analyze(repo).call_edges

        assert edges[0].score > edges[1].score

    def test_a_truncated_but_successful_run_is_surfaced_not_hidden(
        self, repo: Path, tmp_path: Path
    ) -> None:
        document = {
            **DOCUMENT,
            "limits": {
                "applied": {"maxFiles": 10},
                "truncated": True,
                "truncationReasons": ["limit.max-files"],
            },
        }
        analyzer = TypeScriptAnalyzer(command=stub_program(tmp_path, emitting(document)))

        limits = analyzer.analyze(repo).limits

        assert limits.truncated
        assert limits.truncation_reasons == ("limit.max-files",)


class TestFailureModes:
    def failing(self, tmp_path: Path, code: int, payload: dict) -> tuple[str, ...]:
        return stub_program(
            tmp_path,
            "import json, sys\n"
            f"sys.stderr.write(json.dumps({payload!r}))\n"
            f"sys.exit({code})\n",
        )

    def test_a_usage_error_names_itself(self, repo: Path, tmp_path: Path) -> None:
        command = self.failing(tmp_path, 1, {"error": {"code": "usage", "message": "bad flag"}})

        with pytest.raises(AnalyzerError) as caught:
            TypeScriptAnalyzer(command=command).analyze(repo)

        assert caught.value.code == "usage"
        assert "bad flag" in str(caught.value)

    def test_an_exceeded_limit_reports_which_one(self, repo: Path, tmp_path: Path) -> None:
        command = self.failing(
            tmp_path,
            2,
            {"error": {"code": "limit-exceeded", "truncationReasons": ["limit.max-files"]}},
        )

        with pytest.raises(AnalyzerError) as caught:
            TypeScriptAnalyzer(command=command).analyze(repo, strict_limits=True)

        assert caught.value.code == "limit-exceeded"
        assert caught.value.truncation_reasons == ("limit.max-files",)

    def test_an_internal_failure_is_reported_rather_than_swallowed(
        self, repo: Path, tmp_path: Path
    ) -> None:
        command = self.failing(tmp_path, 3, {"error": {"code": "internal", "message": "boom"}})

        with pytest.raises(AnalyzerError) as caught:
            TypeScriptAnalyzer(command=command).analyze(repo)

        assert caught.value.code == "internal"

    def test_a_failure_without_json_on_stderr_still_produces_an_error(
        self, repo: Path, tmp_path: Path
    ) -> None:
        command = stub_program(
            tmp_path, "import sys\nsys.stderr.write('segfault')\nsys.exit(139)\n"
        )

        with pytest.raises(AnalyzerError) as caught:
            TypeScriptAnalyzer(command=command).analyze(repo)

        assert "139" in str(caught.value)

    def test_output_that_is_not_json_is_refused(self, repo: Path, tmp_path: Path) -> None:
        command = stub_program(tmp_path, "print('almost json')\n")

        with pytest.raises(AnalyzerError):
            TypeScriptAnalyzer(command=command).analyze(repo)

    def test_a_subprocess_that_will_not_finish_is_killed(
        self, repo: Path, tmp_path: Path
    ) -> None:
        command = stub_program(tmp_path, "import time\ntime.sleep(30)\n")
        analyzer = TypeScriptAnalyzer(
            command=command,
            limits=TypeScriptAnalyzerLimits(time_budget_seconds=0.2),
            process_timeout_margin=0.3,
        )

        with pytest.raises(AnalyzerTimeout):
            analyzer.analyze(repo)

    def test_a_missing_analyser_is_an_environment_failure_not_a_repository_failure(
        self, repo: Path
    ) -> None:
        analyzer = TypeScriptAnalyzer(command=("this-program-does-not-exist-1234",))

        with pytest.raises(AnalyzerUnavailable):
            analyzer.analyze(repo)


class TestExecutionBoundary:
    def test_only_the_injected_command_is_ever_run(self, repo: Path) -> None:
        """Nothing from the analysed repository decides what gets executed."""
        seen: list[tuple[str, ...]] = []

        def runner(command, **kwargs):
            seen.append(tuple(command))
            return subprocess.CompletedProcess(command, 0, json.dumps(DOCUMENT), "")

        TypeScriptAnalyzer(command=("node", "cli.js"), runner=runner).analyze(repo)

        assert seen[0][:2] == ("node", "cli.js")
        assert len(seen) == 1

    def test_the_subprocess_is_given_a_minimal_environment(self, repo: Path) -> None:
        captured: dict[str, dict[str, str]] = {}

        def runner(command, **kwargs):
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 0, json.dumps(DOCUMENT), "")

        TypeScriptAnalyzer(command=("node", "cli.js"), runner=runner).analyze(repo)

        assert "DEEPSEEK_API_KEY" not in captured["env"]
        assert "MYSQL_PASSWORD" not in captured["env"]
        assert captured["env"].get("NODE_OPTIONS") in (None, "")

    def test_the_subprocess_never_inherits_a_shell(self, repo: Path) -> None:
        captured: dict[str, object] = {}

        def runner(command, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(command, 0, json.dumps(DOCUMENT), "")

        TypeScriptAnalyzer(command=("node", "cli.js"), runner=runner).analyze(repo)

        assert captured.get("shell", False) is False

    def test_the_analysis_root_is_the_working_directory(self, repo: Path) -> None:
        captured: dict[str, object] = {}

        def runner(command, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(command, 0, json.dumps(DOCUMENT), "")

        TypeScriptAnalyzer(command=("node", "cli.js"), runner=runner).analyze(repo)

        assert Path(str(captured["cwd"])) == repo
