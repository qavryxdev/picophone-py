"""Verify HKDF key derivation: both endpoints arrive at the same AES-128 key from PSK + nonces."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.crypto import derive_media_key as _derive_media_key
from picophone.net.media import MediaSecurity


def main() -> None:
    psk = "kocour-modry-stratosfera"
    nonce_a, nonce_b = os.urandom(16), os.urandom(16)
    k_caller = _derive_media_key(psk, nonce_a, nonce_b)
    k_callee = _derive_media_key(psk, nonce_a, nonce_b)
    assert k_caller == k_callee and len(k_caller) == 16
    print(f"OK: derived key {k_caller.hex()} matches on both sides ({len(k_caller)} B)")

    sec = MediaSecurity(key=k_caller)
    aad = b"\x80\x6f\x00\x01\x00\x00\x03\xc0"
    ct = sec.encrypt(b"hello opus", aad)
    pt = sec.decrypt(ct, aad)
    assert pt == b"hello opus"
    print(f"OK: AES-GCM(roundtrip)  ct={len(ct)}B  pt={pt!r}")

    other = MediaSecurity(key=os.urandom(16))
    try:
        other.decrypt(ct, aad)
        print("FAIL: wrong-key decrypt should have raised")
        sys.exit(1)
    except Exception as e:  # noqa: BLE001
        print(f"OK: wrong-key decrypt rejected ({type(e).__name__})")

    empty = _derive_media_key("", nonce_a, nonce_b)
    assert empty == b""
    print("OK: empty PSK -> no key (encryption disabled)")


if __name__ == "__main__":
    main()
