"""What of a repository is worth reading, and what must never be read at all.

Two different concerns share one filter. Most exclusions are about signal: minified
bundles, vendored trees and build output teach a reader nothing about the project. A few
are about safety: a checked-in ``.env`` or private key must not enter the corpus, be
indexed, or end up quoted in a generated tutorial.
"""

from __future__ import annotations

import pytest

from app.ingestion.github.client import TreeEntry
from app.ingestion.github.filters import (
    DEFAULT_MAX_FILE_BYTES,
    ExclusionReason,
    FileFilter,
    is_lfs_pointer,
)


def blob(path: str, size: int = 1024, mode: str = "100644", kind: str = "blob") -> TreeEntry:
    return TreeEntry(path=path, mode=mode, type=kind, sha="a" * 40, size=size)


def reason(path: str, **kwargs) -> ExclusionReason | None:
    return FileFilter().decide(blob(path, **kwargs)).reason


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "src/gateway/service.ts",
        "README.md",
        "docs/guide.mdx",
        "pyproject.toml",
        "Makefile",
        "src/config.yaml",
        ".github/workflows/ci.yml",
    ],
)
def test_source_and_documentation_are_kept(path: str) -> None:
    decision = FileFilter().decide(blob(path))

    assert decision.included
    assert decision.reason is None


@pytest.mark.parametrize(
    "path",
    [
        "assets/logo.png",
        "assets/hero.jpg",
        "fonts/inter.woff2",
        "release/app.zip",
        "bin/server.exe",
        "lib/native.so",
        "data/model.onnx",
        "docs/manual.pdf",
    ],
)
def test_binary_payloads_are_excluded(path: str) -> None:
    assert reason(path) is ExclusionReason.BINARY


@pytest.mark.parametrize(
    "path",
    [
        "node_modules/react/index.js",
        "dist/bundle.js",
        "build/output.js",
        "target/debug/app.rs",
        "out/index.html",
        ".next/static/chunk.js",
        "coverage/lcov-report/index.html",
        "__pycache__/module.cpython-312.pyc",
        ".venv/lib/site-packages/x.py",
        ".git/config",
    ],
)
def test_generated_and_dependency_trees_are_excluded(path: str) -> None:
    assert reason(path) is ExclusionReason.GENERATED


@pytest.mark.parametrize(
    "path",
    ["vendor/github.com/pkg/errors/errors.go", "third_party/zlib/zlib.c", "Pods/AFNetworking/x.m"],
)
def test_vendored_code_is_excluded(path: str) -> None:
    assert reason(path) is ExclusionReason.VENDORED


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "config/.env.production",
        "certs/server.pem",
        "certs/server.key",
        "secrets/id_rsa",
        "secrets/id_ed25519",
        ".npmrc",
        ".netrc",
        "keystore.jks",
        "credentials.json",
        "config/secrets.yml",
        "app.p12",
    ],
)
def test_credentials_are_excluded_wherever_they_are_checked_in(path: str) -> None:
    assert reason(path) is ExclusionReason.SECRET


def test_a_dot_env_example_is_documentation_and_is_kept() -> None:
    """The template is exactly what a tutorial should show; the real file never is."""
    assert FileFilter().decide(blob(".env.example")).included


@pytest.mark.parametrize(
    "path", ["package-lock.json", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock"]
)
def test_lock_files_are_excluded_as_generated(path: str) -> None:
    assert reason(path) is ExclusionReason.GENERATED


def test_minified_bundles_are_excluded() -> None:
    assert reason("static/app.min.js") is ExclusionReason.GENERATED
    assert reason("static/app.js.map") is ExclusionReason.GENERATED


def test_a_file_larger_than_the_budget_is_excluded() -> None:
    assert reason("src/huge.py", size=DEFAULT_MAX_FILE_BYTES + 1) is ExclusionReason.TOO_LARGE


def test_the_size_budget_is_configurable() -> None:
    decision = FileFilter(max_file_bytes=64).decide(blob("src/app.py", size=65))

    assert decision.reason is ExclusionReason.TOO_LARGE


def test_a_submodule_is_excluded_and_never_resolved() -> None:
    assert reason("vendored", mode="160000", kind="commit") is ExclusionReason.SUBMODULE


def test_a_symlink_is_excluded_because_its_target_is_not_ours_to_choose() -> None:
    assert reason("link.py", mode="120000") is ExclusionReason.SYMLINK


def test_a_tree_entry_is_not_a_file() -> None:
    assert reason("src", kind="tree", mode="040000") is ExclusionReason.NOT_A_FILE


@pytest.mark.parametrize(
    "path", ["../escape.py", "/etc/passwd", "src/../../secrets.py", "", "src\\app.py", "a\x00b"]
)
def test_a_path_that_does_not_stay_inside_the_repository_is_excluded(path: str) -> None:
    assert reason(path) is ExclusionReason.UNSAFE_PATH


def test_an_extra_exclusion_can_be_added_without_replacing_the_defaults() -> None:
    filtered = FileFilter(extra_excluded_directories=frozenset({"examples"}))

    assert filtered.decide(blob("examples/demo.py")).reason is ExclusionReason.GENERATED
    assert filtered.decide(blob("node_modules/x/index.js")).reason is ExclusionReason.GENERATED
    assert filtered.decide(blob("src/app.py")).included


class TestGitLfs:
    def test_a_pointer_file_is_recognised_by_its_header(self) -> None:
        pointer = (
            b"version https://git-lfs.github.com/spec/v1\n"
            b"oid sha256:4d7a214614ab2935c943f9e0ff69d22eadbb8f32b1258daaa5e2ca24d17e2393\n"
            b"size 12345\n"
        )

        assert is_lfs_pointer(pointer)

    def test_ordinary_source_is_not_a_pointer(self) -> None:
        assert not is_lfs_pointer(b"def build():\n    return 1\n")

    def test_a_file_that_merely_mentions_lfs_is_not_a_pointer(self) -> None:
        assert not is_lfs_pointer(b"# See https://git-lfs.github.com/spec/v1 for details\n")

    def test_an_empty_file_is_not_a_pointer(self) -> None:
        assert not is_lfs_pointer(b"")


class TestBinaryContent:
    def test_content_with_a_null_byte_is_excluded_after_download(self) -> None:
        decision = FileFilter().inspect_content("src/app.py", b"\x00\x01binary")

        assert decision.reason is ExclusionReason.BINARY

    def test_a_downloaded_lfs_pointer_is_excluded(self) -> None:
        pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 1\n"

        assert FileFilter().inspect_content("data/model.bin", pointer).reason is (
            ExclusionReason.GIT_LFS
        )

    def test_text_survives_the_content_check(self) -> None:
        assert FileFilter().inspect_content("src/app.py", b"print('hi')\n").included

    def test_content_larger_than_the_budget_is_excluded_even_if_the_tree_lied(self) -> None:
        oversized = b"x" * (DEFAULT_MAX_FILE_BYTES + 1)

        assert FileFilter().inspect_content("src/app.py", oversized).reason is (
            ExclusionReason.TOO_LARGE
        )
