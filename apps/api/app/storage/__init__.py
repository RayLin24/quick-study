"""Content-addressed artifact storage.

MySQL keeps only the digest, the relative storage path and the provenance of every
artifact; the bytes themselves live under the configured artifacts root.
"""

from app.storage.artifacts import (
    open_artifact,
    read_artifact,
    record_artifact,
    write_artifact,
)
from app.storage.content_store import (
    ArtifactIntegrityError,
    ArtifactNotFound,
    ArtifactStoreError,
    ContentAddressedStore,
    StoredArtifact,
    UnsafeArtifactPath,
    build_content_store,
)

__all__ = [
    "ArtifactIntegrityError",
    "ArtifactNotFound",
    "ArtifactStoreError",
    "ContentAddressedStore",
    "StoredArtifact",
    "UnsafeArtifactPath",
    "build_content_store",
    "open_artifact",
    "read_artifact",
    "record_artifact",
    "write_artifact",
]
