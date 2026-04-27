"""Media path roundtrip: send N RTP-like packets through MediaSession.make_packet
and feed them back into a peer session. Verify all received with AES-GCM."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.media import MediaSecurity, MediaSession, new_ssrc


def main() -> None:
    received: list[bytes] = []
    sec = MediaSecurity(key=os.urandom(16))
    a = MediaSession(new_ssrc(), sec, lambda _p: None)
    b = MediaSession(new_ssrc(), sec, lambda p: received.append(p))

    N = 50
    for i in range(N):
        wire = a.make_packet(b"opus_payload_" + str(i).encode().rjust(4, b"0"), 960)
        b.feed(wire)

    assert len(received) == N, f"only {len(received)}/{N} received"
    print(f"OK: {N} encrypted media packets roundtripped (first={received[0]!r})")
    print(f"OK: a sent {a.pkts_sent} pkts/{a.bytes_sent} B, b recv {b.pkts_recv} pkts")


if __name__ == "__main__":
    main()
