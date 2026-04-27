from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QProgressBar,
    QSlider, QToolButton, QVBoxLayout, QWidget,
)

from picophone.call import CallController
from picophone.config import Config

log = logging.getLogger(__name__)


class _LED(QLabel):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("led")
        self.setProperty("on", False)

    def set_on(self, on: bool) -> None:
        if bool(self.property("on")) == bool(on):
            return
        self.setProperty("on", on)
        self.style().unpolish(self)
        self.style().polish(self)


class MainWindow(QMainWindow):
    def __init__(self, cfg: Config, controller: CallController) -> None:
        super().__init__()
        self.cfg = cfg
        self.ctrl = controller
        self.setWindowFlag(Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(330, 200)
        self.move(*cfg.ui.window_pos)

        self._drag_offset: QPoint | None = None
        self._call_state = "idle"
        self._build_ui()
        self._apply_skin()
        self._wire()

        self._tick = QTimer(self)
        self._tick.timeout.connect(self._refresh_meters)
        self._tick.start(80)

    # ---------- layout ----------

    def _build_ui(self) -> None:
        root = QWidget(objectName="root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(4)

        title = QHBoxLayout()
        self.title_lbl = QLabel(f"PicoPhone-Py — {self.cfg.net.identity}", objectName="title")
        title.addWidget(self.title_lbl, 1)
        self.btn_min = QToolButton(text="_")
        self.btn_close = QToolButton(text="X")
        for b in (self.btn_min, self.btn_close):
            b.setFixedSize(18, 18)
        title.addWidget(self.btn_min)
        title.addWidget(self.btn_close)
        outer.addLayout(title)

        toolbar = QHBoxLayout()
        toolbar.setSpacing(2)
        self.btn_call = QToolButton(text="CALL")
        self.btn_disc = QToolButton(text="DISC")
        self.btn_msg  = QToolButton(text="MSG")
        self.btn_off  = QToolButton(text="OFF",  checkable=True)
        self.btn_chat = QToolButton(text="CHAT")
        self.btn_log  = QToolButton(text="LOG")
        self.btn_conf = QToolButton(text="CONF")
        for b in (self.btn_call, self.btn_disc, self.btn_msg, self.btn_off,
                  self.btn_chat, self.btn_log, self.btn_conf):
            toolbar.addWidget(b)
        outer.addLayout(toolbar)

        body = QHBoxLayout()
        col = QVBoxLayout()
        self.cb_contact = QComboBox(editable=True)
        self.cb_contact.addItems(self.cfg.net.contacts or ["localhost"])
        col.addWidget(self.cb_contact)

        self.status = QLabel("Idle", objectName="status")
        col.addWidget(self.status)

        meters = QVBoxLayout()
        meters.setSpacing(2)
        mic_row = QHBoxLayout()
        self.mic_led = _LED()
        self.mic_bar = QProgressBar(maximum=100, textVisible=False)
        mic_row.addWidget(QLabel("MIC")); mic_row.addWidget(self.mic_led); mic_row.addWidget(self.mic_bar, 1)
        spk_row = QHBoxLayout()
        self.spk_led = _LED()
        self.spk_bar = QProgressBar(maximum=100, textVisible=False)
        spk_row.addWidget(QLabel("SPK")); spk_row.addWidget(self.spk_led); spk_row.addWidget(self.spk_bar, 1)
        meters.addLayout(mic_row); meters.addLayout(spk_row)
        col.addLayout(meters)
        body.addLayout(col, 1)

        sliders = QHBoxLayout()
        self.sl_mic = QSlider(Qt.Vertical, minimum=0, maximum=1000, value=self.cfg.audio.rec_level)
        self.sl_spk = QSlider(Qt.Vertical, minimum=0, maximum=1000, value=self.cfg.audio.play_volume)
        self.sl_mic.setFixedHeight(70); self.sl_spk.setFixedHeight(70)
        sliders.addWidget(self.sl_mic); sliders.addWidget(self.sl_spk)
        body.addLayout(sliders)
        outer.addLayout(body, 1)

        bottom = QHBoxLayout()
        self.lbl_tx = QLabel("TX:0.0k")
        self.lbl_rx = QLabel("RX:0.0k")
        self.btn_pref  = QToolButton(text="PREF")
        self.btn_about = QToolButton(text="ABOUT")
        bottom.addWidget(self.lbl_tx); bottom.addWidget(self.lbl_rx); bottom.addStretch(1)
        bottom.addWidget(self.btn_pref); bottom.addWidget(self.btn_about)
        outer.addLayout(bottom)

    def _apply_skin(self) -> None:
        qss = (Path(__file__).parent / "skin.qss").read_text(encoding="utf-8")
        self.setStyleSheet(qss)
        root = self.centralWidget()
        root.setProperty("light", self.cfg.ui.light_background)

    def _wire(self) -> None:
        self.btn_close.clicked.connect(self.close)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_call.clicked.connect(self._on_call)
        self.btn_disc.clicked.connect(self.ctrl.hang_up)
        self.btn_off.toggled.connect(self.ctrl.set_muted)
        self.btn_about.clicked.connect(self._on_about)
        self.sl_mic.valueChanged.connect(self._save_mic)
        self.sl_spk.valueChanged.connect(self._save_spk)

        self.ctrl.incoming_invite.connect(self._on_incoming)
        self.ctrl.call_state.connect(self._on_call_state)
        self.ctrl.log_event.connect(self.status.setText)
        self.ctrl.peer_discovered.connect(self._on_peer_discovered)
        self.ctrl.peer_lost.connect(self._on_peer_lost)

    # ---------- slots ----------

    def _on_call(self) -> None:
        idx = self.cb_contact.currentIndex()
        target = (self.cb_contact.itemData(idx) if idx >= 0 else None) \
                 or self.cb_contact.currentText().strip()
        if not target:
            return
        if target not in self.cfg.net.contacts:
            self.cfg.net.contacts.insert(0, target)
            self.cfg.save()
        self.ctrl.place_call(target)

    def _on_peer_discovered(self, identity: str, host: str, port: int) -> None:
        target = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        label = f"{identity}  ({target})"
        for i in range(self.cb_contact.count()):
            if self.cb_contact.itemData(i) == target:
                self.cb_contact.setItemText(i, label)
                return
        self.cb_contact.addItem(label, userData=target)

    def _on_peer_lost(self, identity: str) -> None:
        for i in range(self.cb_contact.count()):
            txt = self.cb_contact.itemText(i)
            if txt.startswith(f"{identity}  ("):
                self.cb_contact.removeItem(i)
                return

    def _on_incoming(self, call_id: str, peer_repr: str) -> None:
        box = QMessageBox(self)
        box.setWindowTitle("Incoming call")
        box.setText(f"Incoming call from {peer_repr}")
        accept = box.addButton("Accept", QMessageBox.AcceptRole)
        box.addButton("Reject", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is accept:
            self.ctrl.accept_call(call_id)
        else:
            self.ctrl.reject_call(call_id)

    def _on_call_state(self, state: str) -> None:
        self._call_state = state
        labels = {"idle": "Idle", "calling": "Calling…", "ringing": "Ringing",
                  "in-call": "In call", "ended": "Idle"}
        self.status.setText(labels.get(state, state))
        self.btn_call.setEnabled(state in ("idle", "ended"))
        self.btn_disc.setEnabled(state in ("calling", "ringing", "in-call"))

    def _on_about(self) -> None:
        QMessageBox.about(
            self, "About PicoPhone-Py",
            "PicoPhone-Py v0.1\n\n"
            "Modern reimplementation of PicoPhone (Aldazabal, 2009).\n"
            "Opus 48 kHz · WebRTC AEC3 · IPv6 · AES-GCM media.",
        )

    def _save_mic(self, v: int) -> None:
        self.cfg.audio.rec_level = v
        self.cfg.save()

    def _save_spk(self, v: int) -> None:
        self.cfg.audio.play_volume = v
        self.cfg.save()

    def _refresh_meters(self) -> None:
        eng = getattr(self.ctrl, "_engine", None)
        if eng is None:
            self.mic_bar.setValue(0); self.spk_bar.setValue(0)
            self.mic_led.set_on(False); self.spk_led.set_on(False)
            return
        mic = _rms_to_pct(eng.tx_rms)
        spk = _rms_to_pct(eng.rx_rms)
        self.mic_bar.setValue(mic); self.spk_bar.setValue(spk)
        self.mic_led.set_on(mic > 5 and not eng.muted)
        self.spk_led.set_on(spk > 5)

    # ---------- frameless drag ----------

    def mousePressEvent(self, e: QMouseEvent) -> None:
        if e.button() == Qt.LeftButton:
            self._drag_offset = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e: QMouseEvent) -> None:
        if self._drag_offset is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:
        self._drag_offset = None
        self.cfg.ui.window_pos = (self.x(), self.y())
        self.cfg.save()

    def closeEvent(self, e) -> None:
        self.cfg.ui.window_pos = (self.x(), self.y())
        self.cfg.save()
        self.ctrl.stop()
        super().closeEvent(e)


def _rms_to_pct(rms_norm: float) -> int:
    if rms_norm <= 1e-5:
        return 0
    db = 20.0 * math.log10(rms_norm + 1e-9)   # -inf..0
    return max(0, min(100, int((db + 60) * 100 / 60)))
