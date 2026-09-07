"""AES-256-GCM vault: round-trip, plaintext migrate, tamper detect. No network."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import secure_store as ss

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"FAIL: {name} {detail}"
    PASS += 1
    print(f"  ok - {name}")


def setup():
    ss.reset_key_cache()
    ss.use_ephemeral_key(b"F" * 32)


def test_json_roundtrip():
    setup()
    p = Path(tempfile.mkdtemp()) / "secret.json"
    payload = {"gemini_api_key": "not-a-real-key", "nested": {"a": 1}}
    ss.write_json(p, payload)
    check("file is encrypted", ss.is_encrypted(p))
    raw = p.read_bytes()
    check("magic header", raw.startswith(ss.MAGIC))
    check("plaintext key not on disk", b"not-a-real-key" not in raw)
    check("roundtrip", ss.read_json(p) == payload)


def test_reads_legacy_plaintext():
    setup()
    p = Path(tempfile.mkdtemp()) / "legacy.json"
    p.write_text('{"os_system": "windows"}', encoding="utf-8")
    check("not encrypted yet", not ss.is_encrypted(p))
    check("plaintext still readable", ss.read_json(p) == {"os_system": "windows"})
    check("seal converts", ss.seal_file(p) is True)
    check("now encrypted", ss.is_encrypted(p))
    check("value survived seal", ss.read_json(p)["os_system"] == "windows")
    check("seal is idempotent", ss.seal_file(p) is False)


def test_tamper_is_rejected():
    setup()
    p = Path(tempfile.mkdtemp()) / "t.json"
    ss.write_json(p, {"ok": True})
    blob = bytearray(p.read_bytes())
    blob[-1] ^= 0xFF
    p.write_bytes(bytes(blob))
    check("tamper returns default", ss.read_json(p, default={"x": 1}) == {"x": 1})


def test_windows_dpapi_vault():
    if sys.platform != "win32":
        check("dpapi skipped", True)
        return
    ss.reset_key_cache()
    p = Path(tempfile.mkdtemp()) / "dpapi.json"
    ss.write_json(p, {"hello": "world"})
    ss.reset_key_cache()
    check("dpapi roundtrip after vault reload", ss.read_json(p) == {"hello": "world"})
    check("still encrypted on disk", ss.is_encrypted(p))


def test_missing_file():
    setup()
    p = Path(tempfile.mkdtemp()) / "nope.json"
    check("missing is default", ss.read_json(p, default=[]) == [])


def test_bytes_and_pem():
    setup()
    p = Path(tempfile.mkdtemp()) / "FRIDAY.key"
    pem = b"-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n"
    ss.write_bytes(p, pem)
    check("key file encrypted", ss.is_encrypted(p))
    check("bytes roundtrip", ss.read_bytes(p) == pem)
    check("pem not in ciphertext", b"BEGIN PRIVATE KEY" not in p.read_bytes())
    tmp = ss.materialize_plaintext(p)
    try:
        check("materialized pem", tmp.read_bytes() == pem)
    finally:
        ss.cleanup_materialized()
        check("temp key deleted", not tmp.exists())


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"{t.__name__}:")
        t()
    print(f"\nALL {PASS} CHECKS PASSED ({len(tests)} tests)")
