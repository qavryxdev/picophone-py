from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def derive_media_key(psk: str, nonce_a: bytes, nonce_b: bytes) -> bytes:
    """HKDF-SHA256(PSK, salt = nonce_a || nonce_b, info = "picophone-media/v1") -> 16 B AES key.
    Empty PSK disables encryption (returns b"")."""
    if not psk:
        return b""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=16,
        salt=nonce_a + nonce_b,
        info=b"picophone-media/v1",
    ).derive(psk.encode("utf-8"))
