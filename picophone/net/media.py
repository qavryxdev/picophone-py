"""RTP-like media framing + AES-128-GCM payload encryption.

Unlike the earlier version, MediaSession does NOT own a UDP socket. It only
encodes outgoing packets (``make_packet``) and decodes incoming bytes
(``feed``). The actual datagrams are sent and received via the signaling
socket (multiplexed by first-byte dispatch in SignalingServer) so a single
firewall hole on the signaling port covers both signaling and media.
"""
from __future__ import annotations

import logging
import os
import struct
from dataclasses import dataclass
from typing import Callable

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

log = logging.getLogger(__name__)

RTP_VERSION = 2
PT_OPUS = 111
HEADER_FMT = "!BBHII"  # ver/pt | flags | seq | ts | ssrc
HEADER_LEN = struct.calcsize(HEADER_FMT)
NONCE_LEN = 12

# First byte of an RTP-like packet: (RTP_VERSION << 6) | (PT_OPUS & 0x7F)
#   = (2 << 6) | (111 & 0x7F) = 0x80 | 0x6F = 0xEF
# JSON signaling datagrams begin with '{' = 0x7B, so the first byte is enough
# to dispatch.
RTP_FIRST_BYTE = (RTP_VERSION << 6) | (PT_OPUS & 0x7F)

PayloadCallback = Callable[[bytes], None]


def is_media_datagram(data: bytes) -> bool:
    """True if the first byte looks like our RTP-like media header."""
    return len(data) >= HEADER_LEN and data[0] == RTP_FIRST_BYTE


@dataclass
class MediaSecurity:
    """Symmetric AES-GCM key (16 B) negotiated out-of-band by signaling. Empty key = unencrypted."""
    key: bytes = b""

    def encrypt(self, payload: bytes, aad: bytes) -> bytes:
        if not self.key:
            return payload
        nonce = os.urandom(NONCE_LEN)
        ct = AESGCM(self.key).encrypt(nonce, payload, aad)
        return nonce + ct

    def decrypt(self, blob: bytes, aad: bytes) -> bytes:
        if not self.key:
            return blob
        return AESGCM(self.key).decrypt(blob[:NONCE_LEN], blob[NONCE_LEN:], aad)


class MediaSession:
    """Stateless-ish RTP-like packetiser. Owns no socket: caller routes bytes."""

    def __init__(self, ssrc: int, sec: MediaSecurity, on_payload: PayloadCallback) -> None:
        self.ssrc = ssrc
        self.sec = sec
        self.on_payload = on_payload
        self._seq = 0
        self._ts = 0
        # diagnostics
        self.pkts_sent = 0
        self.pkts_recv = 0
        self.pkts_decrypt_fail = 0
        self.bytes_sent = 0
        self.bytes_recv = 0

    def make_packet(self, opus_payload: bytes, samples: int) -> bytes:
        ver_pt = RTP_FIRST_BYTE
        hdr = struct.pack(HEADER_FMT, ver_pt, 0, self._seq & 0xFFFF,
                          self._ts & 0xFFFFFFFF, self.ssrc)
        self._seq = (self._seq + 1) & 0xFFFF
        self._ts = (self._ts + samples) & 0xFFFFFFFF
        body = self.sec.encrypt(opus_payload, hdr)
        wire = hdr + body
        self.pkts_sent += 1
        self.bytes_sent += len(wire)
        return wire

    def feed(self, data: bytes) -> None:
        if not is_media_datagram(data):
            return
        self.pkts_recv += 1
        self.bytes_recv += len(data)
        hdr, rest = data[:HEADER_LEN], data[HEADER_LEN:]
        try:
            payload = self.sec.decrypt(rest, hdr)
        except Exception:  # noqa: BLE001
            self.pkts_decrypt_fail += 1
            log.debug("decrypt failed (%d B blob)", len(data))
            return
        self.on_payload(payload)


def new_ssrc() -> int:
    return int.from_bytes(os.urandom(4), "big")
