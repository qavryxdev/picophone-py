"""Media path roundtrip on ::1: send N synthetic Opus-shaped packets, verify all received."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.media import MediaSecurity, open_media


async def main() -> None:
    received: list[bytes] = []
    done = asyncio.Event()
    N = 50

    def on_payload(p: bytes) -> None:
        received.append(p)
        if len(received) >= N:
            done.set()

    sec = MediaSecurity(key=os.urandom(16))   # AES-128-GCM
    t_a, ses_a = await open_media(0, sec, lambda _p: None, bind_v6=True)
    t_b, ses_b = await open_media(0, sec, on_payload,      bind_v6=True)

    port_b = t_b.get_extra_info("socket").getsockname()[1]
    ses_a.peer = ("::1", port_b, 0, 0)

    for i in range(N):
        ses_a.send(b"opus_payload_" + str(i).encode().rjust(4, b"0"), 960)

    try:
        await asyncio.wait_for(done.wait(), timeout=2.0)
    except asyncio.TimeoutError:
        print(f"FAIL: only {len(received)}/{N} received")
        sys.exit(1)

    assert len(received) == N
    print(f"OK: {N} encrypted media packets roundtripped (first={received[0]!r})")
    t_a.close(); t_b.close()


if __name__ == "__main__":
    asyncio.run(main())
