"""Backup / restore the output directory (feature 49 + #47 encryption)."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from utils.backup_crypto import decrypt_bytes, encrypt_bytes, is_encrypted_backup


def _passphrase(explicit: str | None = None) -> str:
    if explicit is not None and str(explicit).strip():
        return str(explicit).strip()
    return (os.getenv("BACKUP_PASSPHRASE") or "").strip()


def backup_output(output_dir: Path, dest: Path | None = None, *, passphrase: str | None = None) -> dict:
    src = Path(output_dir)
    if not src.is_dir():
        raise FileNotFoundError("output 目录不存在")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = Path(dest) if dest else src.parent / f"output-backup-{stamp}"
    archive = shutil.make_archive(str(dest), "zip", root_dir=str(src))
    rec = {"archive": archive, "stamp": stamp, "encrypted": False, "includes": ["output"], "excludes": ["llm_cache", "secrets", ".env"]}
    secret = _passphrase(passphrase)
    if secret:
        blob = encrypt_bytes(Path(archive).read_bytes(), secret)
        enc_path = Path(str(archive) + ".qs1")
        enc_path.write_bytes(blob)
        Path(archive).unlink(missing_ok=True)
        rec["archive"] = str(enc_path)
        rec["encrypted"] = True
    return rec


def restore_output(archive: Path, output_dir: Path, *, passphrase: str | None = None) -> dict:
    src = Path(archive)
    if not src.is_file():
        raise FileNotFoundError("备份包不存在")
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    work = src
    if is_encrypted_backup(src) or src.suffix == ".qs1" or src.name.endswith(".zip.qs1"):
        secret = _passphrase(passphrase)
        plain = decrypt_bytes(src.read_bytes(), secret)
        work = dest.parent / f".qs-restore-{src.stem}.zip"
        work.write_bytes(plain)
    try:
        shutil.unpack_archive(str(work), str(dest))
    finally:
        if work != src and work.exists():
            work.unlink(missing_ok=True)
    return {"restored": str(dest), "from": str(src)}


def list_backups(parent: Path) -> list[str]:
    root = Path(parent)
    return sorted(str(p) for p in list(root.glob("output-backup-*.zip")) + list(root.glob("output-backup-*.qs1")))
