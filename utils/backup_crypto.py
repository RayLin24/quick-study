"""Light passphrase wrap for backup drills (#47). Not a compliance vault."""

from __future__ import annotations

import hashlib
import hmac
import os

MAGIC = b"QS1"
SALT_LEN = 16
MAC_LEN = 32
ITERATIONS = 120_000


def _key_material(passphrase: str, salt: bytes) -> tuple[bytes, bytes]:
    raw = hashlib.pbkdf2_hmac("sha256", (passphrase or "").encode("utf-8"), salt, ITERATIONS, dklen=64)
    return raw[:32], raw[32:]


def _keystream(key: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(hashlib.sha256(key + counter.to_bytes(8, "big")).digest())
        counter += 1
    return bytes(out[:length])


def encrypt_bytes(data: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise ValueError("passphrase required")
    salt = os.urandom(SALT_LEN)
    enc_key, mac_key = _key_material(passphrase, salt)
    cipher = bytes(a ^ b for a, b in zip(data, _keystream(enc_key, len(data))))
    mac = hmac.new(mac_key, MAGIC + salt + cipher, hashlib.sha256).digest()
    return MAGIC + salt + mac + cipher


def decrypt_bytes(blob: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise ValueError("passphrase required")
    if not blob.startswith(MAGIC) or len(blob) < 3 + SALT_LEN + MAC_LEN:
        raise ValueError("not a QS1 backup")
    salt = blob[3 : 3 + SALT_LEN]
    mac = blob[3 + SALT_LEN : 3 + SALT_LEN + MAC_LEN]
    cipher = blob[3 + SALT_LEN + MAC_LEN :]
    enc_key, mac_key = _key_material(passphrase, salt)
    expect = hmac.new(mac_key, MAGIC + salt + cipher, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expect):
        raise ValueError("bad backup passphrase")
    return bytes(a ^ b for a, b in zip(cipher, _keystream(enc_key, len(cipher))))


def is_encrypted_backup(path) -> bool:
    from pathlib import Path

    with Path(path).open("rb") as fh:
        return fh.read(3) == MAGIC
