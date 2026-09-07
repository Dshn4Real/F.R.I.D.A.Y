"""
Encrypted files at rest for FRIDAY.

WHAT THIS PROTECTS
    API keys, OAuth tokens, memory, routines, and the dashboard TLS private
    key used to live as plain JSON/PEM in the project folder — which is on
    OneDrive. Anyone with the folder could read them.

    Every write now stores AES-256-GCM ciphertext. The 32-byte master key
    lives *outside* the project, in %LOCALAPPDATA%/FRIDAY (or ~/.friday),
    wrapped with Windows DPAPI so a copied folder cannot be decrypted on
    another PC or another Windows user.

WHAT THIS DOES NOT PROTECT
    Malware running as you can still call DPAPI. This is disk / sync / theft
    protection, not an anti-malware sandbox.

FILE FORMAT
    b"FRD1" + 12-byte nonce + AES-GCM(ciphertext || 16-byte tag)

Reads still accept leftover plaintext so the first launch can migrate.
Writes are always encrypted. seal_app_data() converts existing files in place
after verifying the round-trip, with no plaintext .bak left behind.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

MAGIC = b"FRD1"
_NONCE_LEN = 12
_AAD = b"FRIDAY-v1"
_KEY_LOCK = threading.Lock()
_master_key: bytes | None = None
_ephemeral: bytes | None = None
_materialized: list[Path] = []

_KNOWN_RELATIVE = (
    Path("config") / "api_keys.json",
    Path("memory") / "long_term.json",
    Path("data") / "spotify.json",
    Path("data") / "chat_automation.json",
    Path("data") / "whatsapp_prefs.json",
    Path("data") / "routines.json",
    Path("data") / "followups.json",
    Path("config") / "certs" / "FRIDAY.key",
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _vault_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        return base / "FRIDAY" / "vault.key"
    return Path.home() / ".friday" / "vault.key"


def _crypto():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM


def crypto_available() -> bool:
    try:
        _crypto()
        return True
    except ImportError:
        return False


def use_ephemeral_key(key: bytes | None = None) -> bytes:
    """Tests only: skip the on-disk vault and use a process-local key."""
    global _ephemeral, _master_key
    if key is not None and len(key) != 32:
        raise ValueError("ephemeral key must be 32 bytes")
    _ephemeral = key if key is not None else os.urandom(32)
    _master_key = _ephemeral
    return _ephemeral


def reset_key_cache() -> None:
    """Tests: drop the cached master key (does not delete the vault file)."""
    global _master_key, _ephemeral
    _master_key = None
    _ephemeral = None


def _dpapi_protect(data: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), buf)
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "FRIDAY vault",
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptProtectData failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    buf = ctypes.create_string_buffer(blob, len(blob))
    blob_in = DATA_BLOB(len(blob), buf)
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(blob_out),
    ):
        raise OSError(f"CryptUnprotectData failed ({ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _protect_key(raw: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi_protect(raw)
    return raw


def _unprotect_key(blob: bytes) -> bytes:
    if sys.platform == "win32":
        return _dpapi_unprotect(blob)
    return blob


def _load_or_create_master_key() -> bytes:
    global _master_key
    with _KEY_LOCK:
        if _master_key is not None:
            return _master_key
        if _ephemeral is not None:
            _master_key = _ephemeral
            return _master_key

        path = _vault_path()
        if path.exists():
            wrapped = path.read_bytes()
            key = _unprotect_key(wrapped)
            if len(key) != 32:
                raise ValueError("vault key is the wrong size")
            _master_key = key
            return _master_key

        key = os.urandom(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_protect_key(key))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        _master_key = key
        return _master_key


def is_encrypted_bytes(raw: bytes) -> bool:
    return raw.startswith(MAGIC)


def is_encrypted(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _encrypt(plain: bytes) -> bytes:
    aesgcm = _crypto()(_load_or_create_master_key())
    nonce = os.urandom(_NONCE_LEN)
    ct = aesgcm.encrypt(nonce, plain, _AAD)
    return MAGIC + nonce + ct


def _decrypt(blob: bytes) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("not a FRIDAY encrypted file")
    body = blob[len(MAGIC):]
    nonce, ct = body[:_NONCE_LEN], body[_NONCE_LEN:]
    if len(nonce) != _NONCE_LEN or not ct:
        raise ValueError("truncated encrypted file")
    aesgcm = _crypto()(_load_or_create_master_key())
    return aesgcm.decrypt(nonce, ct, _AAD)


def read_bytes(path: Path) -> bytes:
    raw = path.read_bytes()
    if is_encrypted_bytes(raw):
        return _decrypt(raw)
    return raw


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not crypto_available():
        print("[Security] cryptography not installed — writing plaintext.")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return
    blob = _encrypt(data)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.is_file() or path.stat().st_size == 0:
        return default
    try:
        return json.loads(read_bytes(path).decode("utf-8"))
    except Exception:
        return default


def write_json(path: Path, obj: Any) -> None:
    payload = json.dumps(obj, indent=2, ensure_ascii=False).encode("utf-8")
    write_bytes(path, payload)


def seal_file(path: Path) -> bool:
    """Encrypt a leftover plaintext file in place. Returns True if it changed."""
    if not path.is_file() or not crypto_available():
        return False
    raw = path.read_bytes()
    if not raw or is_encrypted_bytes(raw):
        return False
    write_bytes(path, raw)
    if read_bytes(path) != raw:
        path.write_bytes(raw)
        raise RuntimeError(f"encrypt verify failed, restored plaintext: {path}")
    return True


def seal_app_data() -> int:
    """Encrypt every known secret file that is still plaintext. Returns count."""
    if not crypto_available():
        print("[Security] cryptography not installed — files stay plaintext.")
        return 0
    root = _project_root()
    n = 0
    for rel in _KNOWN_RELATIVE:
        path = root / rel
        try:
            if seal_file(path):
                n += 1
        except Exception as e:
            print(f"[Security] Could not encrypt {rel}: {e}")
    return n


def materialize_plaintext(path: Path) -> Path:
    """Decrypt to a temp file (uvicorn needs a PEM path). Deleted at exit."""
    data = read_bytes(path)
    fd, name = tempfile.mkstemp(prefix="friday_", suffix=path.suffix)
    os.close(fd)
    dest = Path(name)
    dest.write_bytes(data)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    _materialized.append(dest)
    return dest


def cleanup_materialized() -> None:
    for p in list(_materialized):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    _materialized.clear()


atexit.register(cleanup_materialized)
