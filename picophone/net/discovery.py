from __future__ import annotations

import logging
import socket
from typing import Callable

from zeroconf import IPVersion, ServiceBrowser, ServiceInfo, ServiceListener, Zeroconf

log = logging.getLogger(__name__)

SERVICE_TYPE = "_picophonepy._udp.local."

PeerAdded   = Callable[[str, str, int], None]   # identity, host, port
PeerRemoved = Callable[[str], None]             # identity


class _Listener(ServiceListener):
    def __init__(self, on_added: PeerAdded, on_removed: PeerRemoved, self_identity: str) -> None:
        self.on_added = on_added
        self.on_removed = on_removed
        self.self_identity = self_identity

    def _identity(self, name: str) -> str:
        return name.split("." + SERVICE_TYPE, 1)[0]

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return
        identity = self._identity(name)
        if identity == self.self_identity:
            return
        host = self._best_address(info)
        if host:
            self.on_added(identity, host, info.port)

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        identity = self._identity(name)
        if identity != self.self_identity:
            self.on_removed(identity)

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)

    @staticmethod
    def _best_address(info: ServiceInfo) -> str | None:
        for raw in info.addresses_by_version(IPVersion.V4Only):
            return socket.inet_ntoa(raw)
        for raw in info.addresses_by_version(IPVersion.V6Only):
            return socket.inet_ntop(socket.AF_INET6, raw)
        return None


class Discovery:
    def __init__(self, identity: str, port: int,
                 on_added: PeerAdded, on_removed: PeerRemoved) -> None:
        self.zc = Zeroconf(ip_version=IPVersion.All)
        self.identity = identity
        addrs = self._local_addresses()
        self.info = ServiceInfo(
            type_=SERVICE_TYPE,
            name=f"{identity}.{SERVICE_TYPE}",
            addresses=addrs,
            port=port,
            properties={"v": "1"},
            server=f"{socket.gethostname()}.local.",
        )
        self.zc.register_service(self.info)
        self.browser = ServiceBrowser(self.zc, SERVICE_TYPE, _Listener(on_added, on_removed, identity))

    @staticmethod
    def _local_addresses() -> list[bytes]:
        result: list[bytes] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("8.8.8.8", 80))
                result.append(socket.inet_aton(s.getsockname()[0]))
            except OSError:
                pass
        if not result:
            result.append(socket.inet_aton("127.0.0.1"))
        return result

    def close(self) -> None:
        try:
            self.zc.unregister_service(self.info)
        finally:
            self.zc.close()
