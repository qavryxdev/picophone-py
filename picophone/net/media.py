from __future__ import annotations

import asyncio
import logging
import os
import socket
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

PayloadCallback = Callable[[bytes], None]


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


class MediaSession(asyncio.DatagramProtocol):
    def __init__(self, ssrc: int, sec: MediaSecurity, on_payload: PayloadCallback) -> None:
        self.ssrc = ssrc
        self.sec = sec
        self.on_payload = on_payload
        self.peer: tuple | None = None
        self.transport: asyncio.DatagramTransport | None = None
        self._seq = 0
        self._ts = 0

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        if len(data) < HEADER_LEN:
            return
        hdr, rest = data[:HEADER_LEN], data[HEADER_LEN:]
        try:
            payload = self.sec.decrypt(rest, hdr)
        except Exception:  # noqa: BLE001
            log.debug("decrypt failed from %s", addr)
            return
        self.on_payload(payload)

    def send(self, opus_payload: bytes, samples: int) -> None:
        if self.transport is None or self.peer is None:
            return
        ver_pt = (RTP_VERSION << 6) | (PT_OPUS & 0x7F)
        hdr = struct.pack(HEADER_FMT, ver_pt, 0, self._seq & 0xFFFF, self._ts & 0xFFFFFFFF, self.ssrc)
        self._seq = (self._seq + 1) & 0xFFFF
        self._ts = (self._ts + samples) & 0xFFFFFFFF
        body = self.sec.encrypt(opus_payload, hdr)
        self.transport.sendto(hdr + body, self.peer)


async def open_media(port: int, sec: MediaSecurity, on_payload: PayloadCallback,
                     bind_v6: bool = True, ssrc: int | None = None
                     ) -> tuple[asyncio.DatagramTransport, MediaSession]:
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if bind_v6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if bind_v6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
    else:
        sock.bind(("0.0.0.0", port))
    if ssrc is None:
        ssrc = int.from_bytes(os.urandom(4), "big")
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: MediaSession(ssrc, sec, on_payload), sock=sock,
    )
    return transport, protocol
