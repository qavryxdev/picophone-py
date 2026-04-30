"""End-to-end on loopback with the FULL engine path: synthetic audio packets
emitted from a fake AudioEngine on the caller flow through the multiplexed
signaling/media socket and arrive at the callee's MediaSession.

If this passes, the multiplex code is correct and any real-world failure
is network/firewall/AV between the two machines. If this fails, the bug
is in our code.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import picophone.call as _call
from picophone.call import CallController
from picophone.config import AudioCfg, Config, NetCfg, UiCfg


class FakeEngine:
    """Emits a packet every 20 ms; counts received packets via push_packet."""
    def __init__(self, *_a, **_k):
        self.muted = False
        self.tx_rms = 0.5
        self.rx_rms = 0.0
        self.rtt_ms = 0.0
        self.jitter_ms = 0.0
        self.loss_pct = 0.0
        self._frame_samples = 960
        self.on_packet = _a[1] if len(_a) >= 2 else _k.get("on_packet")
        self._stop = threading.Event()
        self._t = None
        self.received = 0

    def start(self):
        def tick():
            i = 0
            while not self._stop.wait(0.02):
                if self.on_packet:
                    self.on_packet(b"OPUS_FAKE_" + str(i).encode().rjust(8, b"0"))
                i += 1
        self._t = threading.Thread(target=tick, daemon=True)
        self._t.start()

    def stop(self):
        self._stop.set()
        if self._t:
            self._t.join(timeout=1.0)

    def push_packet(self, _p, *, seq=None):
        self.received += 1
        self.rx_rms = 0.3


def main() -> None:
    _call.AudioEngine = FakeEngine  # type: ignore[assignment]

    tmp = Path(os.environ.get("TEMP", ".")) / "picophone-e2e"
    tmp.mkdir(parents=True, exist_ok=True)

    def cfg(identity, port):
        return Config(
            path=tmp / f"{identity}.toml",
            audio=AudioCfg(),
            net=NetCfg(identity=identity, port=port, password="", encrypt=True,
                       bind_v6=True, mdns=False, autoanswer=True),
            ui=UiCfg(),
        )

    a = CallController(cfg("alice", 31010))
    b = CallController(cfg("bob",   31020))
    a.start(); b.start()

    a.place_call("[::1]:31020")

    deadline = time.time() + 8.0
    while time.time() < deadline:
        s_a = a.media_stats(); s_b = b.media_stats()
        if s_a["rx_bytes"] > 1000 and s_b["rx_bytes"] > 1000:
            break
        time.sleep(0.1)

    s_a = a.media_stats(); s_b = b.media_stats()
    print(f"alice  TX={s_a['tx_pkts']:4d} pkts/{s_a['tx_bytes']:6d} B  RX={s_a['rx_pkts']:4d} pkts/{s_a['rx_bytes']:6d} B  peer={s_a['peer']}")
    print(f"bob    TX={s_b['tx_pkts']:4d} pkts/{s_b['tx_bytes']:6d} B  RX={s_b['rx_pkts']:4d} pkts/{s_b['rx_bytes']:6d} B  peer={s_b['peer']}")

    a.hang_up()
    time.sleep(0.5)
    a.stop(); b.stop()

    if s_a["rx_bytes"] > 1000 and s_b["rx_bytes"] > 1000:
        print("OK: multiplex media flows in both directions")
    else:
        print("FAIL: media did not flow in both directions"); sys.exit(1)


if __name__ == "__main__":
    main()
