"""Spawning ``@quick-study/ts-analyzer`` and reading what it says.

The analyser is a separate process because the TypeScript Compiler API is the only thing
that can resolve JavaScript and TypeScript honestly, and it lives in Node. That makes this
module a boundary, and the boundary is drawn tightly.

The command is injected in full. Nothing in the repository being analysed contributes to
it — no ``package.json`` script, no local ``node_modules`` binary, no ``tsconfig``. The
subprocess runs without a shell, with a minimal environment carrying none of the
deployment's secrets, and under a wall-clock timeout that is the only real defence against
an input that stalls the parser itself.

Every documented exit code becomes a distinct exception, so a broken repository, an
exceeded budget and a missing Node install are told apart rather than logged as one.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Final

from app.analysis.model import AnalysisDocument, parse_analysis_document
from app.settings import REPO_ROOT

#: The built CLI. A repository-local binary is deliberately never used.
DEFAULT_COMMAND: Final[tuple[str, ...]] = (
    "node",
    str(PurePosixPath("packages/ts-analyzer/dist/cli.js")),
)

#: Added to the analyser's own time budget before the process is killed outright.
DEFAULT_TIMEOUT_MARGIN_SECONDS: Final = 60.0

#: The subprocess needs a PATH to find Node and nothing else from the deployment.
ENVIRONMENT_ALLOWLIST: Final[tuple[str, ...]] = ("PATH", "SYSTEMROOT", "TMPDIR", "TEMP", "TMP")

EXIT_USAGE: Final = 1
EXIT_LIMIT_EXCEEDED: Final = 2
EXIT_INTERNAL: Final = 3


class AnalyzerError(RuntimeError):
    """Raised when the analyser could not produce a usable document."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "unknown",
        truncation_reasons: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.truncation_reasons = truncation_reasons


class AnalyzerUnavailable(AnalyzerError):
    """Raised when the analyser is not installed. An environment fault, not a repo fault."""


class AnalyzerTimeout(AnalyzerError):
    """Raised when the subprocess outlived its wall-clock budget and was killed."""


@dataclass(frozen=True, slots=True)
class TypeScriptAnalyzerLimits:
    """Budgets handed to the subprocess so it bounds itself."""

    max_files: int = 2000
    max_file_bytes: int = 512 * 1024
    max_total_bytes: int = 48 * 1024 * 1024
    max_directory_depth: int = 24
    time_budget_seconds: float = 60.0


class TypeScriptAnalyzer:
    """Runs the JavaScript/TypeScript analyser over one repository checkout."""

    def __init__(
        self,
        *,
        command: Sequence[str] = DEFAULT_COMMAND,
        limits: TypeScriptAnalyzerLimits | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        process_timeout_margin: float = DEFAULT_TIMEOUT_MARGIN_SECONDS,
        environment: dict[str, str] | None = None,
    ) -> None:
        if not command:
            raise AnalyzerError("an analyser command is required", code="usage")
        self._command = tuple(command)
        self._limits = limits or TypeScriptAnalyzerLimits()
        self._runner = runner
        self._margin = process_timeout_margin
        self._environment = environment

    @property
    def limits(self) -> TypeScriptAnalyzerLimits:
        return self._limits

    def analyze(
        self,
        root: Path | str,
        entries: Sequence[str] = (),
        *,
        strict_limits: bool = False,
    ) -> AnalysisDocument:
        """Analyse ``entries`` inside ``root`` and return the shared document."""
        directory = Path(root).resolve()
        command = self.build_command(directory, entries, strict_limits=strict_limits)
        completed = self._run(command, directory)
        if completed.returncode != 0:
            raise _failure(completed)
        return parse_analysis_document(_document(completed.stdout))

    def build_command(
        self,
        root: Path | str,
        entries: Sequence[str] = (),
        *,
        strict_limits: bool = False,
    ) -> tuple[str, ...]:
        """Build the argument vector. Pure, so the boundary can be inspected in a test."""
        directory = Path(root).resolve()
        arguments: list[str] = [*self._command, "--root", str(directory)]
        for entry in entries:
            relative = _inside(directory, entry)
            kind = "file" if (directory / relative).is_file() else "dir"
            arguments.append(f"--{kind}={relative}")
        arguments.extend(
            [
                f"--max-files={self._limits.max_files}",
                f"--max-file-bytes={self._limits.max_file_bytes}",
                f"--max-total-bytes={self._limits.max_total_bytes}",
                f"--max-directory-depth={self._limits.max_directory_depth}",
                f"--time-budget-ms={int(self._limits.time_budget_seconds * 1000)}",
            ]
        )
        if strict_limits:
            arguments.append("--strict-limits")
        return tuple(arguments)

    def _run(
        self,
        command: tuple[str, ...],
        directory: Path,
    ) -> subprocess.CompletedProcess[str]:
        timeout = self._limits.time_budget_seconds + self._margin
        try:
            return self._runner(
                list(command),
                cwd=str(directory),
                env=self._environment if self._environment is not None else _minimal_environment(),
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except FileNotFoundError as error:
            raise AnalyzerUnavailable(
                f"{command[0]!r} is not installed; build packages/ts-analyzer first",
                code="unavailable",
            ) from error
        except subprocess.TimeoutExpired as error:
            raise AnalyzerTimeout(
                f"the analyser exceeded {timeout:.1f}s and was killed", code="timeout"
            ) from error


def _minimal_environment() -> dict[str, str]:
    """Only what Node needs to start. Application secrets are never forwarded."""
    import os

    return {name: os.environ[name] for name in ENVIRONMENT_ALLOWLIST if name in os.environ}


def _inside(root: Path, entry: str) -> str:
    """Return ``entry`` as a root-relative POSIX path, refusing anything that escapes."""
    if not entry or not entry.strip():
        raise AnalyzerError("an empty entry does not name anything", code="usage")
    windows_view = PureWindowsPath(entry)
    if windows_view.is_absolute() or windows_view.drive or windows_view.root:
        raise AnalyzerError(f"{entry!r} is not relative to the analysis root", code="usage")
    resolved = (root / entry).resolve()
    if resolved != root and root not in resolved.parents:
        raise AnalyzerError(f"{entry!r} escapes the analysis root", code="usage")
    return resolved.relative_to(root).as_posix()


def _document(stdout: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as error:
        raise AnalyzerError(
            f"the analyser did not emit a JSON document: {error}", code="malformed-output"
        ) from error
    if not isinstance(payload, dict):
        raise AnalyzerError(
            f"the analyser emitted {type(payload).__name__}, expected an object",
            code="malformed-output",
        )
    return payload


def _failure(completed: subprocess.CompletedProcess[str]) -> AnalyzerError:
    """Turn a documented exit code plus its stderr JSON into a specific error."""
    error = _stderr_error(completed.stderr)
    code = str(error.get("code") or _code_for(completed.returncode))
    reasons = tuple(str(item) for item in error.get("truncationReasons", []))
    message = str(error.get("message") or completed.stderr.strip() or "no diagnostic")
    return AnalyzerError(
        f"the analyser exited {completed.returncode} ({code}): {message}",
        code=code,
        truncation_reasons=reasons,
    )


def _stderr_error(stderr: str) -> dict[str, Any]:
    try:
        payload = json.loads(stderr or "{}")
    except json.JSONDecodeError:
        return {}
    error = payload.get("error") if isinstance(payload, dict) else None
    return error if isinstance(error, dict) else {}


def _code_for(returncode: int) -> str:
    return {
        EXIT_USAGE: "usage",
        EXIT_LIMIT_EXCEEDED: "limit-exceeded",
        EXIT_INTERNAL: "internal",
    }.get(returncode, "unknown")


def default_command(repo_root: Path | None = None) -> tuple[str, ...]:
    """The default command anchored to this checkout rather than the process directory."""
    root = repo_root or REPO_ROOT
    return ("node", str(root / "packages" / "ts-analyzer" / "dist" / "cli.js"))
