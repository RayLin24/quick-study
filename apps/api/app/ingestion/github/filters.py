"""Which repository files are worth reading, and which must never be read.

Two concerns share one filter. Most exclusions are about signal: a minified bundle, a
vendored dependency tree or a build directory teaches a reader nothing about the project
and would crowd real code out of the evidence budget. A few are about safety: a checked-in
``.env`` or private key must not enter the corpus, be indexed, or be quoted back in a
generated tutorial.

Some decisions can only be made after the bytes arrive — a Git LFS pointer looks like a
small text file in the tree — so the filter is applied twice, once on the tree entry and
once on the content.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

from app.ingestion.github.client import TreeEntry

#: Larger than any hand-written source file; anything bigger is data or generated.
DEFAULT_MAX_FILE_BYTES: Final = 512 * 1024

#: Only the start of a file is scanned for the NUL that marks it binary.
BINARY_SNIFF_BYTES: Final = 8192

_LFS_HEADER: Final = b"version https://git-lfs.github.com/spec/v1"


class ExclusionReason(StrEnum):
    """Why a repository file was left out. Recorded so a run can be reviewed."""

    BINARY = "binary"
    TOO_LARGE = "too_large"
    GENERATED = "generated"
    VENDORED = "vendored"
    SECRET = "secret"  # noqa: S105 - exclusion label, not a credential
    SUBMODULE = "submodule"
    SYMLINK = "symlink"
    NOT_A_FILE = "not_a_file"
    UNSAFE_PATH = "unsafe_path"
    GIT_LFS = "git_lfs_pointer"


GENERATED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {
        ".cache",
        ".git",
        ".gradle",
        ".idea",
        ".mypy_cache",
        ".next",
        ".nuxt",
        ".parcel-cache",
        ".pytest_cache",
        ".ruff_cache",
        ".svelte-kit",
        ".terraform",
        ".tox",
        ".venv",
        "__pycache__",
        "bower_components",
        "build",
        "coverage",
        "dist",
        "htmlcov",
        "jspm_packages",
        "node_modules",
        "obj",
        "out",
        "site-packages",
        "target",
        "venv",
    }
)

VENDORED_DIRECTORIES: Final[frozenset[str]] = frozenset(
    {"vendor", "vendored", "third_party", "thirdparty", "3rdparty", "pods", "external"}
)

GENERATED_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        "cargo.lock",
        "composer.lock",
        "gemfile.lock",
        "go.sum",
        "package-lock.json",
        "pnpm-lock.yaml",
        "poetry.lock",
        "uv.lock",
        "yarn.lock",
    }
)

GENERATED_SUFFIXES: Final[tuple[str, ...]] = (
    ".min.js",
    ".min.css",
    ".js.map",
    ".css.map",
    ".bundle.js",
    ".generated.ts",
    "_pb2.py",
)

SECRET_FILENAMES: Final[frozenset[str]] = frozenset(
    {
        ".dockercfg",
        ".git-credentials",
        ".htpasswd",
        ".netrc",
        ".npmrc",
        ".pgpass",
        ".pypirc",
        "credentials",
        "credentials.json",
        "id_dsa",
        "id_ecdsa",
        "id_ed25519",
        "id_rsa",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)

SECRET_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".ppk", ".kdbx"}
)

#: ``.env.example`` and friends are templates a tutorial should show; the real file is not.
ENVIRONMENT_TEMPLATE_SUFFIXES: Final[tuple[str, ...]] = (
    ".example",
    ".sample",
    ".template",
    ".dist",
    ".defaults",
)

BINARY_EXTENSIONS: Final[frozenset[str]] = frozenset(
    {
        ".7z", ".a", ".ai", ".apk", ".avi", ".bin", ".bmp", ".bz2", ".class", ".ckpt",
        ".dat", ".db", ".dll", ".dmg", ".doc", ".docx", ".dylib", ".eot", ".exe", ".fig",
        ".flac", ".gif", ".gz", ".h5", ".ico", ".img", ".iso", ".jar", ".jpeg", ".jpg",
        ".mdb", ".mkv", ".mov", ".mp3", ".mp4", ".npy", ".npz", ".o", ".obj", ".ogg",
        ".onnx", ".otf", ".pb", ".pdb", ".pdf", ".png", ".ppt", ".pptx", ".psd", ".pt",
        ".pth", ".pyc", ".pyd", ".pyo", ".rar", ".safetensors", ".sketch", ".so",
        ".sqlite", ".sqlite3", ".tar", ".tgz", ".ttf", ".war", ".wasm", ".wav", ".webm",
        ".webp", ".whl", ".woff", ".woff2", ".xls", ".xlsx", ".xz", ".zip",
    }
)


@dataclass(frozen=True, slots=True)
class FileDecision:
    """Whether one file is admitted, and why not when it is not."""

    included: bool
    reason: ExclusionReason | None = None
    detail: str = ""


ADMITTED: Final = FileDecision(included=True)


@dataclass(frozen=True, slots=True)
class FileFilter:
    """The default exclusions, plus whatever a project adds on top of them."""

    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    extra_excluded_directories: frozenset[str] = field(default_factory=frozenset)
    extra_excluded_extensions: frozenset[str] = field(default_factory=frozenset)
    extra_excluded_filenames: frozenset[str] = field(default_factory=frozenset)

    def decide(self, entry: TreeEntry) -> FileDecision:
        """Judge a tree entry, before any bytes are downloaded."""
        if entry.is_submodule:
            return FileDecision(False, ExclusionReason.SUBMODULE, entry.path)
        if entry.is_symlink:
            return FileDecision(False, ExclusionReason.SYMLINK, entry.path)
        if not _is_safe_path(entry.path):
            return FileDecision(False, ExclusionReason.UNSAFE_PATH, entry.path)
        if not entry.is_blob:
            return FileDecision(False, ExclusionReason.NOT_A_FILE, entry.type)
        return self.decide_path(entry.path, size=entry.size)

    def decide_path(self, path: str, *, size: int | None = None) -> FileDecision:
        """Judge a path and a declared size on their own."""
        if not _is_safe_path(path):
            return FileDecision(False, ExclusionReason.UNSAFE_PATH, path)
        segments = path.lower().split("/")
        directories, filename = segments[:-1], segments[-1]

        if (secret := self._secret_reason(filename)) is not None:
            return secret
        if vendored := set(directories) & VENDORED_DIRECTORIES:
            return FileDecision(False, ExclusionReason.VENDORED, sorted(vendored)[0])
        if generated := set(directories) & self._generated_directories:
            return FileDecision(False, ExclusionReason.GENERATED, sorted(generated)[0])
        if filename in GENERATED_FILENAMES or filename.endswith(GENERATED_SUFFIXES):
            return FileDecision(False, ExclusionReason.GENERATED, filename)
        if _extension(filename) in self._binary_extensions:
            return FileDecision(False, ExclusionReason.BINARY, _extension(filename))
        if filename in self.extra_excluded_filenames:
            return FileDecision(False, ExclusionReason.GENERATED, filename)
        if size is not None and size > self.max_file_bytes:
            return FileDecision(False, ExclusionReason.TOO_LARGE, str(size))
        return ADMITTED

    def inspect_content(self, path: str, content: bytes) -> FileDecision:
        """Judge the bytes themselves, which is the only place some answers exist."""
        if is_lfs_pointer(content):
            return FileDecision(False, ExclusionReason.GIT_LFS, path)
        if len(content) > self.max_file_bytes:
            return FileDecision(False, ExclusionReason.TOO_LARGE, str(len(content)))
        if b"\x00" in content[:BINARY_SNIFF_BYTES]:
            return FileDecision(False, ExclusionReason.BINARY, path)
        return ADMITTED

    @property
    def _generated_directories(self) -> frozenset[str]:
        return GENERATED_DIRECTORIES | {name.lower() for name in self.extra_excluded_directories}

    @property
    def _binary_extensions(self) -> frozenset[str]:
        return BINARY_EXTENSIONS | {name.lower() for name in self.extra_excluded_extensions}

    def _secret_reason(self, filename: str) -> FileDecision | None:
        if filename in SECRET_FILENAMES or _extension(filename) in SECRET_EXTENSIONS:
            return FileDecision(False, ExclusionReason.SECRET, filename)
        if filename == ".env" or (
            filename.startswith(".env") and not filename.endswith(ENVIRONMENT_TEMPLATE_SUFFIXES)
        ):
            return FileDecision(False, ExclusionReason.SECRET, filename)
        return None


def is_lfs_pointer(content: bytes) -> bool:
    """Whether these bytes are a Git LFS pointer rather than the file it stands for."""
    return content.startswith(_LFS_HEADER) and b"\noid " in content


def _extension(filename: str) -> str:
    index = filename.rfind(".")
    return filename[index:] if index > 0 else ""


def _is_safe_path(path: str) -> bool:
    if not path or path.startswith("/") or "\\" in path or "\x00" in path:
        return False
    return all(segment not in ("", ".", "..") for segment in path.split("/"))
