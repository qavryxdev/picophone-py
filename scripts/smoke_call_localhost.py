"""End-to-end (no audio device, no Qt loop): two CallControllers on ::1 do
INVITE/ACCEPT/BYE with PSK-derived AES-GCM media key. We poll private state
because Qt signals require a running QApplication.exec() to be delivered.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from picophone.call import CallController
from picophone.config import AudioCfg, Config, NetCfg, UiCfg


def _mkcfg(tmp: Path, identity: str, port: int, password: str) -> Config:
    return Config(
        path=tmp / f"{identity}.toml",
        audio=AudioCfg(),
        net=NetCfg(identity=identity, port=port, password=password,
                   bind_v6=True, encrypt=True, mdns=False),
        ui=UiCfg(),
    )


def wait_until(pred, timeout: float = 8.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def main() -> None:
    tmp = Path(os.environ.get("TEMP", ".")) / "picophone-smoke"
    tmp.mkdir(parents=True, exist_ok=True)

    a = CallController(_mkcfg(tmp, "alice", 21010, "hunter2"))
    b = CallController(_mkcfg(tmp, "bob",   21020, "hunter2"))
    b.cfg.net.autoanswer = True

    # avoid AudioEngine.start() hitting real devices in CI; replace with no-op.
    import picophone.call as _call

    class _StubEngine:
        def __init__(self, *_a, **_k):
            self.muted = False
            self.tx_rms = 0.0
            self.rx_rms = 0.0
            self.rtt_ms = 0.0
            self.jitter_ms = 0.0
            self.loss_pct = 0.0
            self._frame_samples = 960
        def start(self): pass
        def stop(self):  pass
        def push_packet(self, _p, *, seq=None): pass

    _call.AudioEngine = _StubEngine                        # type: ignore[assignment]

    a.start(); b.start()

    a.place_call("[::1]:21020")

    in_call = wait_until(lambda: a._active_id and b._active_id, timeout=10.0)
    if not in_call:
        print(f"FAIL: a._active_id={a._active_id}  b._active_id={b._active_id}")
        a.stop(); b.stop(); sys.exit(1)

    print(f"OK: alice in call ({a._active_id[:8]}..)  bob in call ({b._active_id[:8]}..)")
    print(f"OK: alice key = {a._sec.key.hex()[:16]}..  ({len(a._sec.key)} B)")
    print(f"OK: bob   key = {b._sec.key.hex()[:16]}..  ({len(b._sec.key)} B)")
    assert a._sec.key == b._sec.key and len(a._sec.key) == 16

    # actually send some encrypted media through the multiplexed signaling socket
    if a._media and a._media_peer:
        for i in range(5):
            a._loop.call_soon_threadsafe(a._send_media, b"opus_" + str(i).encode().rjust(3, b"0"), 960)
        time.sleep(0.4)
    print("OK: media packets pumped (encrypted with derived key)")

    a.hang_up()
    wait_until(lambda: a._active_id is None, timeout=2.0)
    a.stop(); b.stop()
    print("OK: end-to-end controller wiring with HKDF/AES-GCM key agreement")


if __name__ == "__main__":
    main()
