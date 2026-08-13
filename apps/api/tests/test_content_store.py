import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from app.storage.content_store import (
    ContentAddressedStore,
    UnsafeArtifactPath,
)

TRAVERSAL_ATTEMPTS = (
    "../escaped.bin",
    "../../escaped.bin",
    "aa/../../escaped.bin",
    "/etc/passwd",
    "\\etc\\passwd",
    "..\\..\\escaped.bin",
    "C:\\Windows\\System32\\drivers\\etc\\hosts",
    "\\\\server\\share\\escaped.bin",
    "\\\\?\\C:\\escaped.bin",
    "with\x00null.bin",
    "",
    "   ",
    ".",
    "..",
)


@pytest.fixture
def store(tmp_path: Path) -> ContentAddressedStore:
    return ContentAddressedStore(tmp_path / "artifacts")


def payload_with_digest_prefix(prefix: str) -> bytes:
    """Find bytes whose digest lands in a chosen fan-out directory."""
    for candidate in range(1 << 20):
        payload = f"payload-{candidate}".encode()
        if hashlib.sha256(payload).hexdigest().startswith(prefix):
            return payload
    raise AssertionError(f"no payload hashed into {prefix!r}")


def link_directory(link: Path, target: Path) -> None:
    """Point ``link`` at ``target``, or skip where the platform allows neither form."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass
    if os.name != "nt":  # pragma: no cover - platform dependent
        pytest.skip("directory links are not permitted here")
    junction = subprocess.run(  # noqa: S603
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if junction.returncode != 0 or not link.exists():  # pragma: no cover - platform dependent
        pytest.skip(f"directory links are not permitted here: {junction.stderr!r}")


def test_root_is_created_eagerly_so_workers_never_race_on_mkdir(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"

    ContentAddressedStore(root)

    assert root.is_dir()


def test_put_bytes_addresses_the_object_by_its_sha256(store: ContentAddressedStore) -> None:
    payload = b"<html>tutorial source</html>"

    stored = store.put_bytes(payload)

    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert stored.size_bytes == len(payload)
    assert store.read_bytes(stored.storage_path) == payload


def test_storage_path_fans_out_on_the_digest_prefix(store: ContentAddressedStore) -> None:
    stored = store.put_bytes(b"payload")
    digest = stored.sha256

    assert stored.storage_path == f"{digest[:2]}/{digest[2:4]}/{digest}"
    assert "\\" not in stored.storage_path


def test_identical_content_is_written_once_and_reported_as_deduplicated(
    store: ContentAddressedStore,
) -> None:
    first = store.put_bytes(b"same bytes")
    second = store.put_bytes(b"same bytes")

    assert first.storage_path == second.storage_path
    assert first.deduplicated is False
    assert second.deduplicated is True


def test_distinct_content_never_shares_a_storage_path(store: ContentAddressedStore) -> None:
    first = store.put_bytes(b"left")
    second = store.put_bytes(b"right")

    assert first.storage_path != second.storage_path


def test_empty_content_is_addressable(store: ContentAddressedStore) -> None:
    stored = store.put_bytes(b"")

    assert stored.size_bytes == 0
    assert store.read_bytes(stored.storage_path) == b""


def test_put_stream_hashes_incrementally_without_buffering_everything(
    store: ContentAddressedStore,
) -> None:
    chunks = [b"chapter-", b"one-", b"markdown"]

    stored = store.put_stream(chunks)

    assert stored.sha256 == hashlib.sha256(b"".join(chunks)).hexdigest()
    assert store.read_bytes(stored.storage_path) == b"".join(chunks)


def test_put_file_ingests_an_existing_file_without_moving_the_source(
    store: ContentAddressedStore,
    tmp_path: Path,
) -> None:
    source = tmp_path / "repo-file.py"
    source.write_bytes(b"print('hello')\n")

    stored = store.put_file(source)

    assert source.is_file()
    assert store.read_bytes(stored.storage_path) == b"print('hello')\n"


def test_no_partial_file_survives_a_failing_stream(store: ContentAddressedStore) -> None:
    def exploding_chunks():
        yield b"first"
        raise RuntimeError("upstream fetch died")

    with pytest.raises(RuntimeError, match="upstream fetch died"):
        store.put_stream(exploding_chunks())

    leftovers = [path for path in store.root.rglob("*") if path.is_file()]
    assert leftovers == []


@pytest.mark.parametrize("candidate", TRAVERSAL_ATTEMPTS)
def test_resolve_rejects_paths_that_leave_the_artifact_root(
    store: ContentAddressedStore,
    candidate: str,
) -> None:
    with pytest.raises(UnsafeArtifactPath):
        store.resolve(candidate)


@pytest.mark.parametrize("candidate", TRAVERSAL_ATTEMPTS)
def test_read_bytes_rejects_paths_that_leave_the_artifact_root(
    store: ContentAddressedStore,
    candidate: str,
) -> None:
    with pytest.raises(UnsafeArtifactPath):
        store.read_bytes(candidate)


def test_traversal_attempt_never_reads_an_existing_outside_file(
    store: ContentAddressedStore,
    tmp_path: Path,
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("do not leak", encoding="utf-8")

    with pytest.raises(UnsafeArtifactPath):
        store.read_bytes(f"../{secret.name}")


def test_resolve_rejects_the_reserved_staging_directory(store: ContentAddressedStore) -> None:
    with pytest.raises(UnsafeArtifactPath):
        store.resolve(f"{ContentAddressedStore.STAGING_DIRECTORY}/leaked.part")


def test_resolve_rejects_symlinks_that_escape_the_root(
    store: ContentAddressedStore,
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not leak", encoding="utf-8")
    link_directory(store.root / "aa", outside)

    with pytest.raises(UnsafeArtifactPath):
        store.resolve("aa/secret.txt")


def test_a_write_never_lands_outside_the_root_through_a_linked_directory(
    store: ContentAddressedStore,
    tmp_path: Path,
) -> None:
    """The fan-out directory is derived from the digest, but it is still a path on disk."""
    payload = payload_with_digest_prefix("aa")
    outside = tmp_path / "outside"
    outside.mkdir()
    link_directory(store.root / "aa", outside)

    with pytest.raises(UnsafeArtifactPath):
        store.put_bytes(payload)

    assert list(outside.rglob("*")) == []


def test_resolve_accepts_a_relative_path_inside_the_root(store: ContentAddressedStore) -> None:
    stored = store.put_bytes(b"payload")

    resolved = store.resolve(stored.storage_path)

    assert resolved.is_file()
    assert resolved.parent.parent.parent == store.root.resolve()


def test_exists_reports_membership_without_raising_for_missing_objects(
    store: ContentAddressedStore,
) -> None:
    stored = store.put_bytes(b"payload")
    missing = ContentAddressedStore.storage_path_for("0" * 64)

    assert store.exists(stored.storage_path) is True
    assert store.exists(missing) is False


def test_read_bytes_raises_artifact_not_found_for_a_valid_but_absent_path(
    store: ContentAddressedStore,
) -> None:
    from app.storage.content_store import ArtifactNotFound

    with pytest.raises(ArtifactNotFound):
        store.read_bytes(ContentAddressedStore.storage_path_for("0" * 64))


def test_verify_detects_content_that_no_longer_matches_its_digest(
    store: ContentAddressedStore,
) -> None:
    from app.storage.content_store import ArtifactIntegrityError

    stored = store.put_bytes(b"payload")
    store.resolve(stored.storage_path).write_bytes(b"tampered")

    store.verify(stored.storage_path, hashlib.sha256(b"tampered").hexdigest())
    with pytest.raises(ArtifactIntegrityError):
        store.verify(stored.storage_path, stored.sha256)


def test_storage_path_for_rejects_values_that_are_not_sha256_digests() -> None:
    with pytest.raises(UnsafeArtifactPath):
        ContentAddressedStore.storage_path_for("../../etc/passwd")
    with pytest.raises(UnsafeArtifactPath):
        ContentAddressedStore.storage_path_for("ABC")


def test_default_store_is_rooted_at_the_configured_artifacts_dir(tmp_path: Path) -> None:
    from app.settings import Settings
    from app.storage.content_store import build_content_store

    settings = Settings(_env_file=None, artifacts_dir=tmp_path / "custom")

    assert build_content_store(settings).root == (tmp_path / "custom").resolve()
