"""Headless launch: build the full Qt window + controller, render once, quit. No audio devices used."""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import picophone.call as _call


class _StubEngine:
    def __init__(self, *_a, **_k):
        self.muted = False; self.tx_rms = 0.0; self.rx_rms = 0.0
        self._frame_samples = 960
    def start(self): pass
    def stop(self):  pass
    def push_packet(self, _p): pass


_call.AudioEngine = _StubEngine  # type: ignore[assignment]

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from picophone.call import CallController
from picophone.config import AudioCfg, Config, NetCfg, UiCfg
from picophone.ui.main_window import MainWindow


def main() -> int:
    cfg = Config(path=Path("picophone-smoke.toml"),
                 audio=AudioCfg(),
                 net=NetCfg(identity="smoketest", port=31676, mdns=False),
                 ui=UiCfg())
    app = QApplication(sys.argv)
    ctrl = CallController(cfg)
    ctrl.start()
    win = MainWindow(cfg, ctrl)
    win.show()

    QTimer.singleShot(800, lambda: (ctrl.stop(), app.quit()))
    rc = app.exec()
    print(f"OK: GUI launched, controller started/stopped cleanly (rc={rc})")
    return rc


if __name__ == "__main__":
    sys.exit(main())
