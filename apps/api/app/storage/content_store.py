from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import IO, Final

from app.settings import Settings, get_settings

_STREAM_CHUNK_SIZE: Final = 1 << 20
_SHA256_HEX: Final = re.compile(r"\A[0-9a-f]{64}\Z")


class ArtifactStoreError(Exception):
    """Base class for every artifact storage failure."""


class UnsafeArtifactPath(ArtifactStoreError):
    """Raised when a storage path could address bytes outside the artifacts root."""


class ArtifactNotFound(ArtifactStoreError):
    """Raised when a well-formed storage path has no object behind it."""


class ArtifactIntegrityError(ArtifactStoreError):
    """Raised when stored bytes no longer hash to the digest recorded in MySQL."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    """Everything MySQL needs to reference bytes that were just written."""

    sha256: str
    storage_path: str
    size_bytes: int
    deduplicated: bool


class ContentAddressedStore:
    """Writes and reads immutable objects keyed by the SHA-256 of their content.

    Writes land in a staging directory first and are renamed into place once the digest
    is known, so a crashed or aborted transfer can never be mistaken for a finished
    artifact. Because the path is derived from the content, re-writing the same bytes is
    idempotent and safe to retry.
    """

    STAGING_DIRECTORY: Final = "_incoming"

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._staging = self._root / self.STAGING_DIRECTORY
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    @staticmethod
    def storage_path_for(sha256: str) -> str:
        """Return the relative POSIX path that owns ``sha256``."""
        digest = sha256.lower()
        if not _SHA256_HEX.match(digest):
            raise UnsafeArtifactPath(f"not a sha256 hex digest: {sha256!r}")
        return f"{digest[:2]}/{digest[2:4]}/{digest}"

    def put_bytes(self, data: bytes) -> StoredArtifact:
        return self.put_stream((data,))

    def put_stream(self, chunks: Iterable[bytes]) -> StoredArtifact:
        digest = hashlib.sha256()
        size = 0
        staged = self._new_staging_path()
        try:
            with staged.open("wb") as handle:
                for chunk in chunks:
                    digest.update(chunk)
                    size += len(chunk)
                    handle.write(chunk)
            return self._promote(staged, digest.hexdigest(), size)
        finally:
            staged.unlink(missing_ok=True)

    def put_file(self, source: Path) -> StoredArtifact:
        def read_in_chunks() -> Iterable[bytes]:
            with Path(source).open("rb") as handle:
                while chunk := handle.read(_STREAM_CHUNK_SIZE):
                    yield chunk

        return self.put_stream(read_in_chunks())

    def resolve(self, storage_path: str) -> Path:
        """Return the absolute path of ``storage_path``, refusing anything outside the root."""
        relative = _validated_relative_path(storage_path)
        if relative.parts[0] == self.STAGING_DIRECTORY:
            raise UnsafeArtifactPath("the staging directory holds unfinished writes")
        resolved = (self._root / relative).resolve()
        if resolved != self._root and self._root not in resolved.parents:
            raise UnsafeArtifactPath(f"{storage_path!r} escapes the artifacts root")
        return resolved

    def exists(self, storage_path: str) -> bool:
        return self.resolve(storage_path).is_file()

    def open(self, storage_path: str) -> IO[bytes]:
        resolved = self.resolve(storage_path)
        try:
            return resolved.open("rb")
        except FileNotFoundError as error:
            raise ArtifactNotFound(storage_path) from error

    def read_bytes(self, storage_path: str) -> bytes:
        with self.open(storage_path) as handle:
            return handle.read()

    def verify(self, storage_path: str, expected_sha256: str) -> None:
        """Raise when the stored bytes stopped matching the digest recorded in MySQL."""
        digest = hashlib.sha256()
        with self.open(storage_path) as handle:
            while chunk := handle.read(_STREAM_CHUNK_SIZE):
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected_sha256.lower():
            raise ArtifactIntegrityError(
                f"{storage_path} hashes to {actual}, expected {expected_sha256}"
            )

    def _new_staging_path(self) -> Path:
        self._staging.mkdir(parents=True, exist_ok=True)
        return self._staging / f"{uuid.uuid4().hex}.part"

    def _promote(self, staged: Path, sha256: str, size: int) -> StoredArtifact:
        """Rename a finished write into the place its digest owns.

        The destination goes through the same check as a read: the path is derived from the
        content, but a linked fan-out directory would still land the bytes outside the root,
        and validating before creating anything means nothing is left behind out there.
        """
        storage_path = self.storage_path_for(sha256)
        target = self.resolve(storage_path)
        if target.is_file():
            return StoredArtifact(sha256, storage_path, size, deduplicated=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)
        return StoredArtifact(sha256, storage_path, size, deduplicated=False)


def _validated_relative_path(storage_path: str) -> PurePosixPath:
    """Reject every spelling of "outside the root" before touching the filesystem.

    Backslashes are treated as separators on all platforms so a Windows-style traversal
    cannot slip through a POSIX deployment as a single odd file name.
    """
    if not isinstance(storage_path, str) or "\x00" in storage_path:
        raise UnsafeArtifactPath("storage paths must be NUL-free strings")
    windows_view = PureWindowsPath(storage_path)
    if windows_view.is_absolute() or windows_view.drive or windows_view.root:
        raise UnsafeArtifactPath(f"{storage_path!r} is not relative")
    candidate = PurePosixPath(storage_path.replace("\\", "/"))
    if candidate.is_absolute():
        raise UnsafeArtifactPath(f"{storage_path!r} is not relative")
    parts = candidate.parts
    if not parts or any(part == ".." or not part.strip() for part in parts):
        raise UnsafeArtifactPath(f"{storage_path!r} is not a usable relative path")
    return candidate


def build_content_store(settings: Settings | None = None) -> ContentAddressedStore:
    return ContentAddressedStore((settings or get_settings()).artifacts_dir)
