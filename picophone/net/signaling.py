from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

log = logging.getLogger(__name__)

PROTOCOL_VERSION = 1


@dataclass
class CallInvite:
    call_id: str
    from_id: str
    addr: tuple
    media_port: int
    codec: str
    nonce_a: bytes                                            # caller's 16-byte salt half


InviteCallback = Callable[[CallInvite], Awaitable[None]]
AcceptCallback = Callable[[str, int, bytes], None]           # call_id, peer_media_port, nonce_b
RejectCallback = Callable[[str, str], None]                  # call_id, reason
ByeCallback    = Callable[[str], None]                       # call_id


class SignalingServer(asyncio.DatagramProtocol):
    """Dual-stack UDP signaling. JSON datagrams; one socket handles IPv4 + IPv6 via V6ONLY=0."""

    def __init__(self, identity: str,
                 on_invite: InviteCallback,
                 on_accept: AcceptCallback | None = None,
                 on_reject: RejectCallback | None = None,
                 on_bye: ByeCallback | None = None) -> None:
        self.identity = identity
        self.on_invite = on_invite
        self.on_accept = on_accept or (lambda _i, _p: None)
        self.on_reject = on_reject or (lambda _i, _r: None)
        self.on_bye    = on_bye    or (lambda _i: None)
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr) -> None:
        try:
            msg = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            log.warning("malformed datagram from %s", addr)
            return
        if msg.get("v") != PROTOCOL_VERSION:
            return
        t = msg.get("t")
        cid = msg.get("id", "")
        if t == "INVITE":
            try:
                nonce_a = base64.b64decode(msg.get("nonce", ""))
            except Exception:  # noqa: BLE001
                nonce_a = b""
            inv = CallInvite(
                call_id=cid,
                from_id=msg.get("from", "?"),
                addr=addr,
                media_port=int(msg.get("media_port", 0)),
                codec=(msg.get("codecs") or ["opus/48000/1"])[0],
                nonce_a=nonce_a,
            )
            asyncio.create_task(self.on_invite(inv))
        elif t == "ACCEPT":
            try:
                nonce_b = base64.b64decode(msg.get("nonce", ""))
            except Exception:  # noqa: BLE001
                nonce_b = b""
            self.on_accept(cid, int(msg.get("media_port", 0)), nonce_b)
        elif t == "REJECT":
            self.on_reject(cid, msg.get("reason", ""))
        elif t == "BYE":
            self.on_bye(cid)
        elif t == "MSG":
            log.info("MSG from %s: %s", msg.get("from"), msg.get("text", ""))

    def send(self, msg: dict, addr) -> None:
        if self.transport is None:
            return
        self.transport.sendto(json.dumps(msg).encode("utf-8"), addr)

    def invite(self, addr, media_port: int, nonce_a: bytes) -> str:
        cid = str(uuid.uuid4())
        self.send({"v": PROTOCOL_VERSION, "t": "INVITE", "id": cid,
                   "from": self.identity, "media_port": media_port,
                   "codecs": ["opus/48000/1"],
                   "nonce": base64.b64encode(nonce_a).decode("ascii")}, addr)
        return cid

    def accept(self, call_id: str, media_port: int, addr, nonce_b: bytes) -> None:
        self.send({"v": PROTOCOL_VERSION, "t": "ACCEPT", "id": call_id,
                   "media_port": media_port, "codec": "opus/48000/1",
                   "nonce": base64.b64encode(nonce_b).decode("ascii")}, addr)

    def reject(self, call_id: str, reason: str, addr) -> None:
        self.send({"v": PROTOCOL_VERSION, "t": "REJECT", "id": call_id,
                   "reason": reason}, addr)

    def bye(self, call_id: str, addr) -> None:
        self.send({"v": PROTOCOL_VERSION, "t": "BYE", "id": call_id}, addr)


async def start_server(port: int, identity: str,
                       on_invite: InviteCallback,
                       bind_v6: bool = True,
                       on_accept: AcceptCallback | None = None,
                       on_reject: RejectCallback | None = None,
                       on_bye: ByeCallback | None = None,
                       ) -> tuple[asyncio.DatagramTransport, SignalingServer]:
    loop = asyncio.get_running_loop()
    family = socket.AF_INET6 if bind_v6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_DGRAM)
    if bind_v6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.bind(("::", port))
    else:
        sock.bind(("0.0.0.0", port))
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: SignalingServer(identity, on_invite, on_accept, on_reject, on_bye),
        sock=sock,
    )
    log.info("Signaling listening on %s port %d (dual-stack=%s)",
             sock.getsockname()[0], port, bind_v6)
    return transport, protocol
