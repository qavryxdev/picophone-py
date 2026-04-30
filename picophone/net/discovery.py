"""Pure-Python UDP-multicast peer discovery for PicoPhone-Py.

Each instance:
  - joins multicast group 239.255.42.42:5354 on every IPv4 interface
  - announces itself with a small JSON datagram every ANNOUNCE_INTERVAL s
  - listens for other instances' announcements
  - expires peers that haven't re-announced within PEER_TTL s

This replaces the zeroconf-based mDNS discovery so that the build pipeline
can produce a single-file PyInstaller exe without Cython ABI mismatches
that zeroconf's compiled extensions cause inside the onefile bootstrap.

The protocol is intentionally tiny: one JSON datagram, no DNS encoding,
no Bonjour/Avahi compatibility — we only need to find other PicoPhone-Py
instances on the LAN.
"""
from __future__ import annotations

import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Callable

log = logging.getLogger(__name__)

MCAST_GROUP    = "239.255.42.42"      # IANA-allocated administratively-scoped block
MCAST_PORT     = 5354
ANNOUNCE_INTERVAL = 5.0               # seconds between announcements
PEER_TTL          = 15.0              # drop a peer if not seen for this long
SCAN_INTERVAL     = 3.0               # how often to expire stale peers
PROTO_TAG         = "picophonepy"
PROTO_VERSION     = 1

PeerAdded   = Callable[[str, str, int], None]   # identity, host, port
PeerRemoved = Callable[[str], None]             # identity


def _best_local_ipv4() -> str:
    """Best-effort 'outward-facing' IPv4 of this host (no packet actually sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 53))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


class Discovery:
    def __init__(self, identity: str, port: int,
                 on_added: PeerAdded, on_removed: PeerRemoved,
                 host_override: str | None = None) -> None:
        self.identity   = identity
        self.port       = port
        self.on_added   = on_added
        self.on_removed = on_removed
        # host_override comes from the STUN client when NAT traversal is on.
        # When unset we fall back to the LAN-only RFC1918 address.
        self.host       = host_override or _best_local_ipv4()

        self._stop = threading.Event()
        self._peers: dict[str, tuple[str, int, float]] = {}   # identity -> (host, port, last_seen)
        self._sock_rx = self._open_rx_socket()
        self._sock_tx = self._open_tx_socket()
        self._t_rx = threading.Thread(target=self._rx_loop,       name="picophone-disc-rx", daemon=True)
        self._t_tx = threading.Thread(target=self._announce_loop, name="picophone-disc-tx", daemon=True)
        self._t_gc = threading.Thread(target=self._gc_loop,       name="picophone-disc-gc", daemon=True)
        self._t_rx.start(); self._t_tx.start(); self._t_gc.start()

    # ---------- sockets ----------

    @staticmethod
    def _open_rx_socket() -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # On Windows there's no SO_REUSEPORT; SO_REUSEADDR is enough for multicast joins.
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", MCAST_PORT))
        mreq = struct.pack("=4s4s", socket.inet_aton(MCAST_GROUP), socket.inet_aton("0.0.0.0"))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        s.settimeout(1.0)
        return s

    @staticmethod
    def _open_tx_socket() -> socket.socket:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)   # 4 hops is plenty for LAN
        # Loopback so two instances on the same host see each other.
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        return s

    # ---------- threads ----------

    def _announce_loop(self) -> None:
        instance_uid = f"{self.identity}:{os.getpid()}"
        msg = json.dumps({
            "v": PROTO_VERSION, "ann": PROTO_TAG, "uid": instance_uid,
            "id": self.identity, "host": self.host, "port": self.port,
        }).encode("utf-8")
        while not self._stop.is_set():
            try:
                self._sock_tx.sendto(msg, (MCAST_GROUP, MCAST_PORT))
            except OSError as e:
                log.debug("announce send failed: %s", e)
            self._stop.wait(ANNOUNCE_INTERVAL)

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self._sock_rx.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if msg.get("ann") != PROTO_TAG or msg.get("v") != PROTO_VERSION:
                continue
            identity = msg.get("id") or "?"
            uid      = msg.get("uid") or identity
            host     = msg.get("host") or addr[0]
            port     = int(msg.get("port") or 0)
            if uid == f"{self.identity}:{os.getpid()}":
                continue   # ignore our own loopback announcements
            now = time.monotonic()
            prev = self._peers.get(uid)
            self._peers[uid] = (host, port, now)
            if prev is None or prev[:2] != (host, port):
                try:
                    self.on_added(identity, host, port)
                except Exception:  # noqa: BLE001
                    log.exception("on_added callback raised")

    def _gc_loop(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(SCAN_INTERVAL)
            if self._stop.is_set():
                break
            now = time.monotonic()
            expired = [u for u, (_h, _p, t) in self._peers.items() if (now - t) > PEER_TTL]
            for uid in expired:
                _h, _p, _t = self._peers.pop(uid)
                identity = uid.split(":", 1)[0]
                try:
                    self.on_removed(identity)
                except Exception:  # noqa: BLE001
                    log.exception("on_removed callback raised")

    # ---------- shutdown ----------

    def close(self) -> None:
        self._stop.set()
        for s in (self._sock_rx, self._sock_tx):
            try: s.close()
            except OSError: pass
        for t in (self._t_rx, self._t_tx, self._t_gc):
            t.join(timeout=2.0)
