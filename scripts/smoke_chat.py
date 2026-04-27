"""Verify MSG signaling roundtrip: alice sends a chat line, bob receives it."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.signaling import CallInvite, start_server


async def main() -> None:
    received: asyncio.Future = asyncio.get_running_loop().create_future()

    async def on_invite_x(_inv: CallInvite) -> None: ...

    def on_msg_b(from_id: str, text: str, addr: tuple) -> None:
        print(f"[B] MSG <{from_id}> {text!r} from {addr[0]}:{addr[1]}")
        if not received.done():
            received.set_result((from_id, text))

    t_a, srv_a = await start_server(0, "alice", on_invite_x, bind_v6=True)
    t_b, srv_b = await start_server(0, "bob",   on_invite_x, bind_v6=True, on_msg=on_msg_b)

    port_b = t_b.get_extra_info("socket").getsockname()[1]
    srv_a.msg("hello bob, ready for tests?", ("::1", port_b, 0, 0))

    from_id, text = await asyncio.wait_for(received, timeout=2.0)
    assert from_id == "alice" and text == "hello bob, ready for tests?"
    print("OK: MSG roundtripped via signaling")
    t_a.close(); t_b.close()


if __name__ == "__main__":
    asyncio.run(main())
