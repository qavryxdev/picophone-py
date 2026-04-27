from __future__ import annotations

import logging
import math
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QMouseEvent
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QProgressBar, QSlider, QToolButton, QVBoxLayout, QWidget,
)

from picophone.call import CallController
from picophone.config import Config
from picophone.ui.chat_window import ChatWindow

log = logging.getLogger(__name__)


class _LED(QLabel):
    """LED + click-to-mute toggle (mimics original PicoPhone's MIC/SPK lights)."""
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("led")
        self.setProperty("on", False)
        self.setProperty("muted", False)
        self.setCursor(Qt.PointingHandCursor)

    def set_on(self, on: bool) -> None:
        if bool(self.property("on")) == bool(on):
            return
        self.setProperty("on", on)
        self._refresh()

    def set_muted(self, muted: bool) -> None:
        self.setProperty("muted", muted)
        self._refresh()

    def _refresh(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


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
        self._chats: dict[str, ChatWindow] = {}
        self._prev_stats: dict | None = None
        self._prev_stats_t = 0.0
        self._incoming_dialog = None
        self._incoming_call_id: str | None = None

        self._ring = QSoundEffect(self)
        ring_path = Path(__file__).parent.parent.parent / "assets" / "ringin.wav"
        if ring_path.exists():
            self._ring.setSource(QUrl.fromLocalFile(str(ring_path)))
            self._ring.setLoopCount(-2)            # QSoundEffect.Infinite, as int (Qt 6.11 strict)
            self._ring.setVolume(0.7)

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
        self.btn_off.toggled.connect(self._on_off_toggle)
        self.btn_chat.clicked.connect(self._on_chat)
        self.btn_msg.clicked.connect(self._on_quick_msg)
        self.btn_pref.clicked.connect(self._on_prefs)
        self.btn_about.clicked.connect(self._on_about)
        self.sl_mic.valueChanged.connect(self._on_mic_slider)
        self.sl_spk.valueChanged.connect(self._on_spk_slider)
        self.mic_led.clicked.connect(self._toggle_mic_mute)
        self.spk_led.clicked.connect(self._toggle_spk_mute)

        self.ctrl.incoming_invite.connect(self._on_incoming)
        self.ctrl.call_state.connect(self._on_call_state)
        self.ctrl.log_event.connect(self.status.setText)
        self.ctrl.peer_discovered.connect(self._on_peer_discovered)
        self.ctrl.peer_lost.connect(self._on_peer_lost)
        self.ctrl.incoming_msg.connect(self._on_incoming_msg)
        self.ctrl.notification.connect(self._on_notification)
        self.ctrl.incoming_cancelled.connect(self._on_incoming_cancelled)

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
        # If we're already showing a ringing dialog, treat additional INVITEs
        # as busy — Qt modal dialogs don't stack reliably and a second
        # nested exec() can deadlock or crash.
        if self._incoming_dialog is not None or self._call_state == "in-call":
            self.ctrl.reject_call(call_id, "busy")
            return

        # Bring the main window forward so the user actually sees the prompt.
        self.showNormal()
        self.raise_()
        self.activateWindow()
        try:
            self._ring.play()
        except Exception:  # noqa: BLE001
            pass

        box = QMessageBox(self)
        box.setWindowTitle("Incoming call")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Incoming call from {peer_repr}")
        accept = box.addButton("Accept", QMessageBox.AcceptRole)
        box.addButton("Reject", QMessageBox.RejectRole)
        box.setWindowFlag(Qt.WindowStaysOnTopHint, True)

        self._incoming_dialog = box
        self._incoming_call_id = call_id
        try:
            box.exec()
        finally:
            self._incoming_dialog = None
            self._incoming_call_id = None
            try:
                self._ring.stop()
            except Exception:  # noqa: BLE001
                pass

        clicked = box.clickedButton()
        if clicked is None:
            return                          # remote cancelled, no action needed
        if clicked is accept:
            self.ctrl.accept_call(call_id)
        else:
            self.ctrl.reject_call(call_id)

    def _on_incoming_cancelled(self, call_id: str) -> None:
        if self._incoming_call_id == call_id and self._incoming_dialog is not None:
            try:
                self._ring.stop()
            except Exception:  # noqa: BLE001
                pass
            # close() returns from exec() with no clicked button -> caller of
            # _on_incoming sees clickedButton() is None and skips both
            # accept_call and reject_call (the caller already cancelled).
            self._incoming_dialog.done(0)
            self.status.setText("Caller cancelled")

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
            "Opus 48 kHz · FDAF AEC · IPv6 · AES-GCM media.",
        )

    # ---------- chat / msg ----------

    def _selected_target(self) -> tuple[str, str] | None:
        """Returns (peer_identity, peer_target) for the currently picked contact."""
        idx = self.cb_contact.currentIndex()
        target = (self.cb_contact.itemData(idx) if idx >= 0 else None) \
                 or self.cb_contact.currentText().strip()
        if not target:
            return None
        text = self.cb_contact.itemText(idx) if idx >= 0 else target
        identity = text.split("  (", 1)[0] if "  (" in text else target
        return identity, target

    def _open_chat(self, peer_identity: str, peer_target: str) -> ChatWindow:
        chat = self._chats.get(peer_identity)
        if chat is None:
            chat = ChatWindow(peer_identity, peer_target, self.cfg.net.identity, self)
            chat.send_clicked.connect(self.ctrl.send_msg)
            self._chats[peer_identity] = chat
        else:
            chat.update_target(peer_target)
        chat.show(); chat.raise_(); chat.activateWindow()
        return chat

    def _on_chat(self) -> None:
        sel = self._selected_target()
        if sel is None:
            QMessageBox.information(self, "Chat", "Pick a contact first.")
            return
        self._open_chat(*sel)

    def _on_quick_msg(self) -> None:
        sel = self._selected_target()
        if sel is None:
            QMessageBox.information(self, "MSG", "Pick a contact first.")
            return
        identity, target = sel
        text, ok = QInputDialog.getText(self, "Send message", f"Message to {identity}:",
                                        QLineEdit.Normal, "")
        if ok and text.strip():
            self.ctrl.send_msg(target, text.strip())
            self._open_chat(identity, target).append(self.cfg.net.identity, text.strip())

    def _on_incoming_msg(self, from_id: str, text: str, peer_addr: str) -> None:
        chat = self._chats.get(from_id)
        if chat is None:
            chat = ChatWindow(from_id, peer_addr, self.cfg.net.identity, self)
            chat.send_clicked.connect(self.ctrl.send_msg)
            self._chats[from_id] = chat
        else:
            chat.update_target(peer_addr)
        chat.append(from_id, text)
        chat.show(); chat.raise_()

    def _on_prefs(self) -> None:
        from picophone.ui.prefs_dialog import PrefsDialog
        dlg = PrefsDialog(self.cfg, self)
        if dlg.exec() == dlg.DialogCode.Accepted:
            self.cfg.save()
            QMessageBox.information(
                self, "PicoPhone-Py",
                "Settings saved. Restart the app for port/identity/device changes "
                "to take effect.",
            )

    def _on_mic_slider(self, v: int) -> None:
        self.cfg.audio.rec_level = v
        self.cfg.save()
        eng = getattr(self.ctrl, "_engine", None)
        if eng is not None:
            eng.in_gain = max(0.0, min(1.0, v / 1000.0))

    def _on_spk_slider(self, v: int) -> None:
        self.cfg.audio.play_volume = v
        self.cfg.save()
        eng = getattr(self.ctrl, "_engine", None)
        if eng is not None:
            eng.out_gain = max(0.0, min(1.0, v / 1000.0))

    def _toggle_mic_mute(self) -> None:
        eng = getattr(self.ctrl, "_engine", None)
        if eng is None:
            return
        eng.muted = not eng.muted
        self.btn_off.setChecked(eng.muted)
        self.mic_led.set_muted(eng.muted)

    def _toggle_spk_mute(self) -> None:
        eng = getattr(self.ctrl, "_engine", None)
        if eng is None:
            return
        eng.spk_muted = not eng.spk_muted
        self.spk_led.set_muted(eng.spk_muted)

    def _on_off_toggle(self, on: bool) -> None:
        self.ctrl.set_muted(on)
        self.mic_led.set_muted(on)

    def _on_notification(self, kind: str, message: str) -> None:
        icon = QMessageBox.Information
        if kind in ("error", "lost"):
            icon = QMessageBox.Warning
        box = QMessageBox(self)
        box.setIcon(icon)
        box.setWindowTitle("PicoPhone-Py")
        box.setText(message)
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _refresh_meters(self) -> None:
        import time as _t
        eng = getattr(self.ctrl, "_engine", None)
        if eng is None:
            self.mic_bar.setValue(0); self.spk_bar.setValue(0)
            self.mic_led.set_on(False); self.spk_led.set_on(False)
            self.lbl_tx.setText("TX:0.0k"); self.lbl_rx.setText("RX:0.0k")
            return
        mic = _rms_to_pct(eng.tx_rms)
        spk = _rms_to_pct(eng.rx_rms)
        self.mic_bar.setValue(mic); self.spk_bar.setValue(spk)
        self.mic_led.set_on(mic > 5 and not eng.muted)
        self.spk_led.set_on(spk > 5)

        stats = self.ctrl.media_stats()
        now = _t.monotonic()
        if self._prev_stats is None:
            self._prev_stats = stats; self._prev_stats_t = now
            return
        dt = max(now - self._prev_stats_t, 0.001)
        if dt < 0.5:
            return
        d_tx = stats["tx_bytes"] - self._prev_stats["tx_bytes"]
        d_rx = stats["rx_bytes"] - self._prev_stats["rx_bytes"]
        tx_kbps = (d_tx * 8 / 1000.0) / dt
        rx_kbps = (d_rx * 8 / 1000.0) / dt
        self.lbl_tx.setText(f"TX:{tx_kbps:.1f}k")
        self.lbl_rx.setText(f"RX:{rx_kbps:.1f}k")
        # diagnostic info on hover
        peer = stats["peer"]
        peer_str = f"{peer[0]}:{peer[1]}" if peer else "no peer"
        tip = (f"TX {stats['tx_pkts']} pkts {stats['tx_bytes']} B / "
               f"RX {stats['rx_pkts']} pkts {stats['rx_bytes']} B  "
               f"decrypt-fail {stats['decrypt_fail']}  "
               f"peer {peer_str}  encrypted={stats['key_set']}")
        self.lbl_tx.setToolTip(tip); self.lbl_rx.setToolTip(tip)
        self._prev_stats = stats; self._prev_stats_t = now

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
