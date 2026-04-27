from __future__ import annotations

import asyncio
import logging
import os
import socket
import threading
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QObject, Signal

from picophone.audio.engine import AudioEngine
from picophone.config import Config
from picophone.crypto import derive_media_key as _derive_media_key
from picophone.net.media import MediaSecurity, MediaSession, open_media
from picophone.net.signaling import (
    PROTOCOL_VERSION, CallInvite, SignalingServer, start_server,
)

log = logging.getLogger(__name__)


@dataclass
class _Pending:
    call_id: str
    peer: tuple
    waiter: asyncio.Future
    nonce_a: bytes


class CallController(QObject):
    """Orchestrates GUI ↔ signaling ↔ media. asyncio loop runs in its own thread."""

    incoming_invite = Signal(str, str)         # call_id, peer_repr
    call_state      = Signal(str)              # "idle" | "ringing" | "calling" | "in-call" | "ended"
    log_event       = Signal(str)              # human-readable line for status bar / log
    peer_discovered = Signal(str, str, int)    # identity, host, port
    peer_lost       = Signal(str)              # identity
    incoming_msg    = Signal(str, str, str)    # from_id, text, peer_addr_str

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sig_transport = None
        self._sig: SignalingServer | None = None
        self._media_transport = None
        self._media: MediaSession | None = None
        self._engine: AudioEngine | None = None
        self._sec = MediaSecurity()
        self._discovery = None
        self._pending: dict[str, _Pending] = {}
        self._pending_invites: dict[str, CallInvite] = {}
        self._active_id: str | None = None
        self._active_peer: tuple | None = None

    # -------- lifecycle --------

    def start(self) -> None:
        ready = threading.Event()

        def runner() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._loop.create_task(self._bootstrap(ready))
            self._loop.run_forever()

        self._thread = threading.Thread(target=runner, name="picophone-net", daemon=True)
        self._thread.start()
        ready.wait(timeout=3.0)

    async def _bootstrap(self, ready: threading.Event) -> None:
        try:
            self._sig_transport, self._sig = await start_server(
                self.cfg.net.port, self.cfg.net.identity,
                on_invite=self._on_invite,
                bind_v6=self.cfg.net.bind_v6,
                on_accept=self.on_accept,
                on_reject=self.on_reject,
                on_bye=self.on_bye,
                on_msg=self.on_msg,
            )
        except OSError as e:
            log.error("Cannot bind signaling port %d: %s", self.cfg.net.port, e)
        if self.cfg.net.mdns:
            try:
                from picophone.net.discovery import Discovery
                self._discovery = Discovery(
                    self.cfg.net.identity, self.cfg.net.port,
                    on_added=lambda i, h, p: self.peer_discovered.emit(i, h, p),
                    on_removed=lambda i: self.peer_lost.emit(i),
                )
            except Exception:
                log.exception("mDNS discovery failed to start")
        ready.set()

    def stop(self) -> None:
        if self._loop is None or not self._loop.is_running():
            return
        fut = asyncio.run_coroutine_threadsafe(self._teardown(), self._loop)
        try:
            fut.result(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)
        self._loop = None

    async def _teardown(self) -> None:
        await self._end_call()
        if self._discovery:
            try: self._discovery.close()
            except Exception: log.exception("discovery.close failed")
            self._discovery = None
        if self._sig_transport:
            self._sig_transport.close()
            self._sig_transport = None

    # -------- public API (GUI thread) --------

    def place_call(self, target: str) -> None:
        self._submit(self._place_call(target))

    def accept_call(self, call_id: str) -> None:
        self._submit(self._accept_call(call_id))

    def reject_call(self, call_id: str, reason: str = "busy") -> None:
        self._submit(self._reject_call(call_id, reason))

    def hang_up(self) -> None:
        self._submit(self._end_call())

    def set_muted(self, muted: bool) -> None:
        if self._engine:
            self._engine.muted = muted

    # -------- async impl --------

    async def _place_call(self, target: str) -> None:
        if self._sig is None:
            self.log_event.emit("Signaling not ready")
            return
        try:
            peer = await _resolve(target, self.cfg.net.port, prefer_v6=self.cfg.net.bind_v6)
        except OSError as e:
            self.log_event.emit(f"Cannot resolve {target}: {e}")
            return

        media_port = self._pick_media_port()
        nonce_a = os.urandom(16)
        await self._open_media(media_port)
        self.call_state.emit("calling")
        self.log_event.emit(f"Calling {target} -> {peer[0]}:{peer[1]}")

        cid = self._sig.invite(peer, media_port, nonce_a)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self._pending[cid] = _Pending(cid, peer, waiter, nonce_a)

        try:
            accepted_peer_media, nonce_b = await asyncio.wait_for(waiter, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(cid, None)
            await self._end_call()
            self.log_event.emit("No answer from remote PicoPhone")
            return

        self._sec.key = _derive_media_key(self.cfg.net.password, nonce_a, nonce_b) if self.cfg.net.encrypt else b""
        await self._connect_media_to(peer[0], accepted_peer_media)
        self._active_id, self._active_peer = cid, peer
        self.call_state.emit("in-call")
        self.log_event.emit(f"Call accepted (encrypted={bool(self._sec.key)})")

    async def _accept_call(self, call_id: str) -> None:
        inv = self._pending_invites.pop(call_id, None)
        if inv is None or self._sig is None:
            return
        media_port = self._pick_media_port()
        nonce_b = os.urandom(16)
        self._sec.key = _derive_media_key(self.cfg.net.password, inv.nonce_a, nonce_b) if self.cfg.net.encrypt else b""
        await self._open_media(media_port)
        self._sig.accept(call_id, media_port, inv.addr, nonce_b)
        await self._connect_media_to(inv.addr[0], inv.media_port)
        self._active_id, self._active_peer = call_id, inv.addr
        self.call_state.emit("in-call")
        self.log_event.emit(f"Connected to {inv.from_id} (encrypted={bool(self._sec.key)})")

    async def _reject_call(self, call_id: str, reason: str) -> None:
        inv = self._pending_invites.pop(call_id, None)
        if inv is None or self._sig is None:
            return
        self._sig.reject(call_id, reason, inv.addr)
        self.call_state.emit("idle")

    async def _end_call(self) -> None:
        if self._active_id and self._active_peer and self._sig:
            self._sig.bye(self._active_id, self._active_peer)
        self._active_id = self._active_peer = None
        if self._engine:
            try: self._engine.stop()
            except Exception: log.exception("engine.stop failed")
            self._engine = None
        if self._media_transport:
            self._media_transport.close()
        self._media_transport = self._media = None
        self.call_state.emit("idle")

    # -------- signaling callbacks --------

    async def _on_invite(self, inv: CallInvite) -> None:
        self._pending_invites[inv.call_id] = inv
        peer_repr = f"{inv.from_id} ({inv.addr[0]}:{inv.addr[1]})"
        self.log_event.emit(f"Incoming call from {peer_repr}")
        if self.cfg.net.autoanswer:
            await self._accept_call(inv.call_id)
        else:
            self.call_state.emit("ringing")
            self.incoming_invite.emit(inv.call_id, peer_repr)

    def on_accept(self, call_id: str, peer_media_port: int, nonce_b: bytes) -> None:
        p = self._pending.pop(call_id, None)
        if p and not p.waiter.done():
            p.waiter.set_result((peer_media_port, nonce_b))

    def on_reject(self, call_id: str, reason: str) -> None:
        p = self._pending.pop(call_id, None)
        if p and not p.waiter.done():
            p.waiter.set_exception(OSError(f"rejected: {reason}"))

    def on_bye(self, call_id: str) -> None:
        if call_id == self._active_id:
            asyncio.create_task(self._end_call())
            self.log_event.emit("Remote hung up")

    def on_msg(self, from_id: str, text: str, addr: tuple) -> None:
        host, port = addr[0], addr[1]
        peer_str = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        self.incoming_msg.emit(from_id, text, peer_str)
        self.log_event.emit(f"MSG <{from_id}> {text[:40]}")

    def send_msg(self, target: str, text: str) -> None:
        self._submit(self._send_msg(target, text))

    async def _send_msg(self, target: str, text: str) -> None:
        if self._sig is None:
            return
        try:
            peer = await _resolve(target, self.cfg.net.port, prefer_v6=self.cfg.net.bind_v6)
        except OSError as e:
            self.log_event.emit(f"Cannot resolve {target}: {e}")
            return
        self._sig.msg(text, peer)

    # -------- media wiring --------

    async def _open_media(self, port: int) -> None:
        self._media_transport, self._media = await open_media(
            port, self._sec, self._on_media_payload, bind_v6=self.cfg.net.bind_v6,
        )
        self._engine = AudioEngine(self.cfg.audio, self._on_audio_packet)
        try:
            self._engine.start()
        except Exception as e:  # noqa: BLE001
            log.exception("Audio engine failed to start")
            self.log_event.emit(f"Audio start failed: {e}")
            self._engine = None

    async def _connect_media_to(self, host: str, port: int) -> None:
        if self._media is None:
            return
        addr = await _resolve(host, port, prefer_v6=self.cfg.net.bind_v6)
        self._media.peer = addr

    def _on_audio_packet(self, payload: bytes) -> None:
        # called from PortAudio thread → hop to asyncio loop
        if self._loop and self._media:
            self._loop.call_soon_threadsafe(self._media.send, payload, self._engine._frame_samples)

    def _on_media_payload(self, payload: bytes) -> None:
        if self._engine:
            self._engine.push_packet(payload)

    # -------- helpers --------

    def _submit(self, coro) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _pick_media_port(self) -> int:
        return self.cfg.net.port + 1


async def _resolve(target: str, default_port: int, prefer_v6: bool) -> tuple:
    """Parse host[:port] / [ipv6]:port and resolve. Returns sockaddr suitable for sendto."""
    host, port = _split_host_port(target, default_port)
    family = socket.AF_INET6 if prefer_v6 else socket.AF_UNSPEC
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, family=family, type=socket.SOCK_DGRAM)
    if not infos:
        raise OSError(f"no addrinfo for {target}")
    if prefer_v6:
        for fam, _t, _p, _c, sa in infos:
            if fam == socket.AF_INET6:
                return sa
        # fall back: map v4 to v4-mapped v6 so a v6-only socket can sendto
        fam, _t, _p, _c, sa = infos[0]
        ip4 = sa[0]
        return (f"::ffff:{ip4}", sa[1], 0, 0)
    return infos[0][4]


def _split_host_port(s: str, default_port: int) -> tuple[str, int]:
    s = s.strip()
    if s.startswith("["):
        end = s.index("]")
        host = s[1:end]
        rest = s[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") else default_port
        return host, port
    if s.count(":") == 1:
        h, p = s.split(":")
        return h, int(p)
    return s, default_port
