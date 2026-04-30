"""Minimal RFC5389 STUN client — single Binding Request, single response.

We only need one thing: ask a public STUN server "what's my outward-facing
IP+port?", parse the XOR-MAPPED-ADDRESS attribute and return it.  No
authentication, no fingerprint, no fallback servers.  All we want is to
advertise a NAT-traversable host:port instead of a LAN-only RFC1918 IP.

If anything goes wrong (timeout, malformed reply, server unreachable) we
return None — the caller falls back to the local-IPv4 advertisement.
"""
from __future__ import annotations

import logging
import os
import socket
import struct
from typing import Optional

log = logging.getLogger(__name__)

MAGIC_COOKIE = 0x2112A442
BINDING_REQUEST = 0x0001
BINDING_SUCCESS = 0x0101
ATTR_MAPPED_ADDRESS     = 0x0001
ATTR_XOR_MAPPED_ADDRESS = 0x0020
FAMILY_IPV4 = 0x01
FAMILY_IPV6 = 0x02


def _split_host_port(server: str, default_port: int = 3478) -> tuple[str, int]:
    if ":" in server and not server.startswith("["):
        h, p = server.rsplit(":", 1)
        try:
            return h, int(p)
        except ValueError:
            pass
    return server, default_port


def discover_public(server: str = "stun.l.google.com:19302",
                    timeout: float = 2.0) -> Optional[tuple[str, int]]:
    """Send one Binding Request, parse XOR-MAPPED-ADDRESS, return (ip, port).

    Returns None on any error.  Synchronous; callers should run on a
    background thread or accept ~timeout seconds of blocking.
    """
    host, port = _split_host_port(server)
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror as e:
        log.info("STUN: cannot resolve %s: %s", server, e)
        return None
    if not infos:
        return None

    fam, _, _, _, sa = infos[0]
    txid = os.urandom(12)
    req = struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE) + txid

    sock = socket.socket(fam, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(req, sa)
        data, _addr = sock.recvfrom(2048)
    except OSError as e:
        log.info("STUN: %s: %s", server, e)
        return None
    finally:
        sock.close()

    if len(data) < 20:
        return None
    msg_type, length, cookie = struct.unpack("!HHI", data[:8])
    if cookie != MAGIC_COOKIE or data[8:20] != txid or msg_type != BINDING_SUCCESS:
        log.info("STUN: unexpected response from %s (type=0x%04x)", server, msg_type)
        return None

    # Walk attributes; stop on the first XOR-MAPPED-ADDRESS (preferred)
    # or fall back to plain MAPPED-ADDRESS.
    pos = 20
    end = 20 + length
    mapped: Optional[tuple[str, int]] = None
    while pos + 4 <= end:
        atype, alen = struct.unpack("!HH", data[pos:pos + 4])
        pos += 4
        if pos + alen > end:
            break
        body = data[pos:pos + alen]
        pos += alen + (-alen % 4)        # pad to 4-byte boundary

        if atype == ATTR_XOR_MAPPED_ADDRESS and len(body) >= 8:
            family = body[1]
            xor_port = struct.unpack("!H", body[2:4])[0] ^ (MAGIC_COOKIE >> 16)
            if family == FAMILY_IPV4 and len(body) >= 8:
                ip_bytes = bytes(b ^ c for b, c in zip(body[4:8],
                                                      struct.pack("!I", MAGIC_COOKIE)))
                ip = socket.inet_ntop(socket.AF_INET, ip_bytes)
                return (ip, xor_port)
            if family == FAMILY_IPV6 and len(body) >= 20:
                key = struct.pack("!I", MAGIC_COOKIE) + txid
                ip_bytes = bytes(b ^ c for b, c in zip(body[4:20], key))
                ip = socket.inet_ntop(socket.AF_INET6, ip_bytes)
                return (ip, xor_port)
        elif atype == ATTR_MAPPED_ADDRESS and len(body) >= 8 and mapped is None:
            family = body[1]
            mport = struct.unpack("!H", body[2:4])[0]
            if family == FAMILY_IPV4:
                mapped = (socket.inet_ntop(socket.AF_INET, body[4:8]), mport)
            elif family == FAMILY_IPV6 and len(body) >= 20:
                mapped = (socket.inet_ntop(socket.AF_INET6, body[4:20]), mport)
    return mapped
