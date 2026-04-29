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
from picophone.net.media import MediaSecurity, MediaSession, new_ssrc
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
    notification    = Signal(str, str)         # kind ("info"/"error"/"lost"), message
    incoming_cancelled = Signal(str)           # call_id — caller hung up before we accepted

    def __init__(self, cfg: Config) -> None:
        super().__init__()
        self.cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._sig_transport = None
        self._sig: SignalingServer | None = None
        self._media: MediaSession | None = None
        self._media_peer: tuple | None = None
        self._engine: AudioEngine | None = None
        self._sec = MediaSecurity()
        self._discovery = None
        self._pending: dict[str, _Pending] = {}
        self._keepalive_task: asyncio.Task | None = None
        self._last_pong: float = 0.0
        self._pending_invites: dict[str, CallInvite] = {}
        self._ringing_tasks: dict[str, asyncio.Task] = {}    # cid -> ringing-watch keepalive
        self._ringing_pongs: dict[str, float] = {}           # cid -> last PONG monotonic time
        self._invite_retx: dict[str, asyncio.Task] = {}      # cid -> INVITE retransmit task (caller side)
        self._accepted_replies: dict[str, tuple] = {}        # cid -> (port, addr, nonce_b) for ACCEPT retransmits
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
                on_media=self._on_media_datagram,
                on_pong=self.on_pong,
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
            try: self._sig_transport.close()
            except Exception: pass
            self._sig_transport = None
            self._sig = None

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

    def media_stats(self) -> dict:
        m = self._media
        return {
            "tx_pkts":  m.pkts_sent  if m else 0,
            "rx_pkts":  m.pkts_recv  if m else 0,
            "tx_bytes": m.bytes_sent if m else 0,
            "rx_bytes": m.bytes_recv if m else 0,
            "decrypt_fail": m.pkts_decrypt_fail if m else 0,
            "peer":    self._media_peer,
            "key_set": bool(self._sec.key),
            "engine_running": self._engine is not None,
        }

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

        nonce_a = os.urandom(16)
        # Don't start the audio engine yet — wait for ACCEPT.  Otherwise the
        # mic captures during ringing and any spurious audio (system beeps,
        # keystrokes, even an unintended _send_media to a stale peer) leaks
        # before the callee has agreed to the call.
        self.call_state.emit("calling")
        self.log_event.emit(f"Calling {target} -> {peer[0]}:{peer[1]}")

        # Multiplex: signaling and media share the signaling port.
        cid = self._sig.invite(peer, self.cfg.net.port, nonce_a)
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future = loop.create_future()
        self._pending[cid] = _Pending(cid, peer, waiter, nonce_a)
        self._invite_retx[cid] = asyncio.create_task(
            self._invite_retransmit(cid, peer, nonce_a, waiter)
        )

        try:
            _accepted_peer_media, nonce_b = await asyncio.wait_for(waiter, timeout=30.0)
        except asyncio.TimeoutError:
            self._pending.pop(cid, None)
            self._cancel_invite_retx(cid)
            await self._end_call()
            self.log_event.emit("No answer from remote PicoPhone")
            self.notification.emit("error", f"No answer from {target}.\n"
                                            "Remote PicoPhone may be offline or unreachable.")
            return
        except OSError as e:
            self._cancel_invite_retx(cid)
            await self._end_call()
            self.log_event.emit(str(e))
            return
        self._cancel_invite_retx(cid)

        self._sec.key = _derive_media_key(self.cfg.net.password, nonce_a, nonce_b) if self.cfg.net.encrypt else b""
        # Now that the callee has accepted, open the audio engine and route
        # media to them.  Any frames captured before this point are dropped.
        self._open_media()
        self._media_peer = peer
        self._active_id, self._active_peer = cid, peer
        self._start_keepalive()
        self.call_state.emit("in-call")
        self.log_event.emit(f"Call accepted (encrypted={bool(self._sec.key)})")

    async def _accept_call(self, call_id: str) -> None:
        inv = self._pending_invites.pop(call_id, None)
        if inv is None or self._sig is None:
            return
        self._stop_ringing_watch(call_id)
        nonce_b = os.urandom(16)
        self._sec.key = _derive_media_key(self.cfg.net.password, inv.nonce_a, nonce_b) if self.cfg.net.encrypt else b""
        self._open_media()
        self._sig.accept(call_id, self.cfg.net.port, inv.addr, nonce_b)
        self._accepted_replies[call_id] = (self.cfg.net.port, inv.addr, nonce_b)
        self._media_peer = inv.addr
        self._active_id, self._active_peer = call_id, inv.addr
        self._start_keepalive()
        self.call_state.emit("in-call")
        self.log_event.emit(f"Connected to {inv.from_id} (encrypted={bool(self._sec.key)})")

    async def _reject_call(self, call_id: str, reason: str) -> None:
        inv = self._pending_invites.pop(call_id, None)
        self._stop_ringing_watch(call_id)
        if inv is None or self._sig is None:
            return
        self._sig.reject(call_id, reason, inv.addr)
        self.call_state.emit("idle")

    async def _end_call(self) -> None:
        self._stop_keepalive()
        # 1) Active (already-ACCEPT'd) call: notify peer.
        if self._active_id and self._active_peer and self._sig:
            self._sig.bye(self._active_id, self._active_peer)
            self._accepted_replies.pop(self._active_id, None)
        # 2) In-flight outbound INVITEs (caller pressed DISC during ringing
        #    before peer accepted): tell the peer to stop ringing.
        if self._sig:
            for cid, p in list(self._pending.items()):
                try:
                    self._sig.bye(cid, p.peer)
                except Exception:  # noqa: BLE001
                    pass
                if not p.waiter.done():
                    p.waiter.cancel()
                self._cancel_invite_retx(cid)
        self._pending.clear()
        self._active_id = self._active_peer = None
        self._media_peer = None
        if self._engine:
            try: self._engine.stop()
            except Exception: log.exception("engine.stop failed")
            self._engine = None
        self._media = None
        self.call_state.emit("idle")

    # -------- signaling callbacks --------

    async def _on_invite(self, inv: CallInvite) -> None:
        # Self-call loopback: this is our own INVITE bouncing back.  Two
        # AudioEngines (caller + callee) on the same default audio device
        # would crash PortAudio.  Drop silently.
        if inv.call_id in self._pending:
            log.info("INVITE rx is our own loopback cid=%s; dropping", inv.call_id)
            return
        # Idempotent: caller may retransmit INVITE if our ACCEPT got dropped.
        # If we already accepted this cid, just resend the ACCEPT.
        prev = self._accepted_replies.get(inv.call_id)
        if prev is not None and self._sig is not None:
            log.info("INVITE retransmit for already-accepted cid=%s; resending ACCEPT", inv.call_id)
            self._sig.accept(inv.call_id, prev[0], prev[1], prev[2])
            return
        if inv.call_id in self._pending_invites:
            log.info("INVITE retransmit while still ringing cid=%s", inv.call_id)
            return
        self._pending_invites[inv.call_id] = inv
        peer_repr = f"{inv.from_id} ({inv.addr[0]}:{inv.addr[1]})"
        self.log_event.emit(f"Incoming call from {peer_repr}")
        if self.cfg.net.autoanswer:
            await self._accept_call(inv.call_id)
            return
        self.call_state.emit("ringing")
        self.incoming_invite.emit(inv.call_id, peer_repr)
        # Watch the caller while we ring: ping them every 3 s, abort the
        # ring if they don't reply within 9 s (caller force-quit, network
        # dropped, BYE got lost in transit).
        self._ringing_tasks[inv.call_id] = asyncio.create_task(
            self._ringing_watch(inv.call_id, inv.addr)
        )

    async def _ringing_watch(self, call_id: str, addr: tuple) -> None:
        import time as _t
        self._ringing_pongs[call_id] = _t.monotonic()
        try:
            while call_id in self._pending_invites:
                await asyncio.sleep(3.0)
                if call_id not in self._pending_invites:
                    return
                if self._sig:
                    self._sig.ping(call_id, addr)
                if _t.monotonic() - self._ringing_pongs.get(call_id, 0.0) > 9.0:
                    self._pending_invites.pop(call_id, None)
                    self.incoming_cancelled.emit(call_id)
                    self.log_event.emit("Caller went away (no PONG)")
                    self.call_state.emit("idle")
                    return
        except asyncio.CancelledError:
            pass
        finally:
            self._ringing_pongs.pop(call_id, None)
            self._ringing_tasks.pop(call_id, None)

    def _stop_ringing_watch(self, call_id: str) -> None:
        t = self._ringing_tasks.pop(call_id, None)
        if t and not t.done():
            t.cancel()
        self._ringing_pongs.pop(call_id, None)

    def on_accept(self, call_id: str, peer_media_port: int, nonce_b: bytes) -> None:
        p = self._pending.pop(call_id, None)
        if p is None:
            log.warning("on_accept: no pending invite for cid=%s (already resolved or unknown)", call_id)
            return
        if p.waiter.done():
            log.warning("on_accept: waiter already done for cid=%s", call_id)
            return
        log.info("on_accept: waking waiter cid=%s peer_port=%d", call_id, peer_media_port)
        p.waiter.set_result((peer_media_port, nonce_b))

    def on_reject(self, call_id: str, reason: str) -> None:
        p = self._pending.pop(call_id, None)
        if p and not p.waiter.done():
            p.waiter.set_exception(OSError(f"rejected: {reason}"))
        self.notification.emit("info", f"Call rejected by remote ({reason or 'busy'}).")

    def on_bye(self, call_id: str) -> None:
        # 1) Active call ended.
        if call_id == self._active_id:
            asyncio.create_task(self._end_call())
            self.log_event.emit("Remote hung up")
            self.notification.emit("info", "The remote party hung up.")
            return
        # 2) Caller cancelled an INVITE we hadn't accepted yet — stop ringing.
        inv = self._pending_invites.pop(call_id, None)
        if inv is not None:
            self._stop_ringing_watch(call_id)
            self.incoming_cancelled.emit(call_id)
            self.log_event.emit(f"{inv.from_id} cancelled the call")
            self.call_state.emit("idle")
            return
        # 3) Callee rejected/cancelled our outbound INVITE — wake the waiter.
        p = self._pending.pop(call_id, None)
        if p and not p.waiter.done():
            p.waiter.set_exception(OSError("cancelled"))

    def on_pong(self, call_id: str, _addr: tuple) -> None:
        import time as _t
        now = _t.monotonic()
        if call_id == self._active_id:
            self._last_pong = now
        if call_id in self._ringing_pongs:
            self._ringing_pongs[call_id] = now

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

    def _open_media(self) -> None:
        """Set up media session that shares the signaling socket (multiplexed)."""
        self._media = MediaSession(new_ssrc(), self._sec, self._on_media_payload)
        self._engine = AudioEngine(self.cfg.audio, self._on_audio_packet)
        try:
            self._engine.start()
        except Exception as e:  # noqa: BLE001
            log.exception("Audio engine failed to start")
            short = type(e).__name__
            if "PortAudio" in short or "device" in str(e).lower():
                self.log_event.emit("No audio device — call has no sound. "
                                     "Enable a sound card in VM settings, or run on host.")
            else:
                self.log_event.emit(f"Audio start failed: {e}")
            self._engine = None

    def _on_audio_packet(self, payload: bytes) -> None:
        """PortAudio thread -> asyncio thread: build packet, send via signaling socket."""
        if self._loop and self._media and self._engine:
            samples = self._engine._frame_samples
            self._loop.call_soon_threadsafe(self._send_media, payload, samples)

    def _send_media(self, opus_payload: bytes, samples: int) -> None:
        if not (self._media and self._media_peer and self._sig_transport):
            return
        wire = self._media.make_packet(opus_payload, samples)
        try:
            self._sig_transport.sendto(wire, self._media_peer)
        except OSError as e:
            log.debug("media sendto failed: %s", e)

    def _on_media_datagram(self, data: bytes, addr: tuple) -> None:
        """Called by SignalingServer for first-byte-binary datagrams."""
        if self._media is None:
            return
        if self._media.pkts_recv == 0:
            log.info("First media datagram received from %s (%d bytes, first byte=0x%02x)",
                     addr, len(data), data[0])
        self._media.feed(data)

    def _on_media_payload(self, payload: bytes) -> None:
        if self._engine:
            self._engine.push_packet(payload)

    # -------- helpers --------

    def _submit(self, coro) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    INVITE_RETX_INTERVAL = 1.5
    INVITE_RETX_MAX = 4

    async def _invite_retransmit(self, cid: str, peer: tuple, nonce_a: bytes,
                                 waiter: asyncio.Future) -> None:
        try:
            for _ in range(self.INVITE_RETX_MAX):
                await asyncio.sleep(self.INVITE_RETX_INTERVAL)
                if waiter.done() or self._sig is None:
                    return
                self._sig.invite_retransmit(cid, peer, self.cfg.net.port, nonce_a)
        except asyncio.CancelledError:
            pass

    def _cancel_invite_retx(self, cid: str) -> None:
        t = self._invite_retx.pop(cid, None)
        if t and not t.done():
            t.cancel()

    # -------- keepalive --------

    KEEPALIVE_INTERVAL = 3.0     # send PING every 3 s
    KEEPALIVE_TIMEOUT  = 12.0    # drop call after 12 s without PONG

    def _start_keepalive(self) -> None:
        import time as _t
        self._last_pong = _t.monotonic()
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    def _stop_keepalive(self) -> None:
        if self._keepalive_task and not self._keepalive_task.done():
            self._keepalive_task.cancel()
        self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        import time as _t
        try:
            while self._active_id and self._sig and self._active_peer:
                await asyncio.sleep(self.KEEPALIVE_INTERVAL)
                if not (self._active_id and self._active_peer and self._sig):
                    return
                self._sig.ping(self._active_id, self._active_peer)
                if _t.monotonic() - self._last_pong > self.KEEPALIVE_TIMEOUT:
                    self.log_event.emit("Connection lost (keepalive timeout)")
                    self.notification.emit("lost", "Connection to the remote party was lost.\n"
                                                   "(no keepalive reply)")
                    await self._end_call()
                    return
        except asyncio.CancelledError:
            pass


async def _resolve(target: str, default_port: int, prefer_v6: bool) -> tuple:
    """Parse host[:port] / [ipv6]:port and resolve. Returns sockaddr suitable for sendto.

    When the signaling socket is dual-stack v6 (V6ONLY=0) we always need a v6
    sockaddr — pure v6 for AAAA hosts, ``::ffff:a.b.c.d`` for A hosts. Windows'
    getaddrinfo with family=AF_INET6 on a literal IPv4 string raises
    WSAHOST_NOT_FOUND, so we resolve with AF_UNSPEC and map afterwards.
    """
    host, port = _split_host_port(target, default_port)
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_DGRAM)
    except socket.gaierror as e:
        raise OSError(f"no addrinfo for {target}: {e}") from e
    if not infos:
        raise OSError(f"no addrinfo for {target}")

    if prefer_v6:
        for fam, _t, _p, _c, sa in infos:
            if fam == socket.AF_INET6:
                return sa
        for fam, _t, _p, _c, sa in infos:
            if fam == socket.AF_INET:
                return (f"::ffff:{sa[0]}", sa[1], 0, 0)
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
