"""Bare-metal signaling roundtrip on ::1 — no Qt, no audio. Just verifies INVITE/ACCEPT/BYE."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.signaling import CallInvite, start_server


async def main() -> None:
    loop = asyncio.get_running_loop()
    got_invite: asyncio.Future = loop.create_future()
    got_accept: asyncio.Future = loop.create_future()
    got_bye:    asyncio.Future = loop.create_future()
    nonce_b = os.urandom(16)

    async def on_invite_b(inv: CallInvite) -> None:
        print(f"[B] INVITE from {inv.from_id} {inv.addr} media_port={inv.media_port} nonce_a={inv.nonce_a.hex()[:8]}..")
        got_invite.set_result(inv)
        srv_b.accept(inv.call_id, 22000, inv.addr, nonce_b)

    def on_accept_a(cid: str, port: int, nb: bytes) -> None:
        print(f"[A] ACCEPT cid={cid[:8]}.. peer_media_port={port} nonce_b={nb.hex()[:8]}..")
        got_accept.set_result((cid, port, nb))

    def on_bye_a(cid: str) -> None:
        print(f"[A] BYE cid={cid[:8]}..")
        got_bye.set_result(cid)

    async def on_invite_a(inv: CallInvite) -> None:
        pass

    t_a, srv_a = await start_server(0, "alice", on_invite_a, bind_v6=True, on_accept=on_accept_a, on_bye=on_bye_a)
    t_b, srv_b = await start_server(0, "bob",   on_invite_b, bind_v6=True)

    port_b = t_b.get_extra_info("socket").getsockname()[1]
    nonce_a = os.urandom(16)
    cid = srv_a.invite(("::1", port_b, 0, 0), 21000, nonce_a)
    print(f"[A] sent INVITE cid={cid[:8]}.. -> ::1:{port_b}")

    inv = await asyncio.wait_for(got_invite, timeout=2.0)
    cid2, port_acc, nb = await asyncio.wait_for(got_accept, timeout=2.0)
    assert cid2 == cid and nb == nonce_b
    assert inv.nonce_a == nonce_a

    srv_b.bye(inv.call_id, inv.addr)
    await asyncio.wait_for(got_bye, timeout=2.0)

    print("OK: INVITE -> ACCEPT -> BYE with nonce exchange")
    t_a.close(); t_b.close()


if __name__ == "__main__":
    asyncio.run(main())
