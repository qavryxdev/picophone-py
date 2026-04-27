from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QGroupBox, QLineEdit, QSpinBox, QTabWidget, QVBoxLayout,
)

from picophone.config import Config

log = logging.getLogger(__name__)


def _list_devices(kind: str) -> list[tuple[int, str]]:
    """Return [(index, label)] for kind='input' or 'output'. Empty if sounddevice missing."""
    try:
        import sounddevice as sd
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[int, str]] = []
    for i, d in enumerate(sd.query_devices()):
        ch = d.get("max_input_channels" if kind == "input" else "max_output_channels", 0)
        if ch > 0:
            ha = d.get("hostapi", -1)
            try:
                ha_name = sd.query_hostapis(ha)["name"]
            except Exception:  # noqa: BLE001
                ha_name = "?"
            out.append((i, f"{d['name']} ({ha_name})"))
    return out


class PrefsDialog(QDialog):
    def __init__(self, cfg: Config, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("PicoPhone-Py — Preferences")
        self.resize(440, 460)
        self._build()
        self._load()

    # -------- layout --------

    def _build(self) -> None:
        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        tabs.addTab(self._build_network(),  "Network")
        tabs.addTab(self._build_audio(),    "Audio")
        tabs.addTab(self._build_security(), "Security")

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        root.addWidget(bb)

    def _build_network(self):
        page = QGroupBox()
        f = QFormLayout(page)
        self.ed_identity   = QLineEdit()
        self.sb_port       = QSpinBox(); self.sb_port.setRange(1024, 65535)
        self.cb_v6         = QCheckBox("Listen on IPv6 (dual-stack v4 + v6)")
        self.cb_autoanswer = QCheckBox("Auto-answer incoming calls")
        self.cb_mdns       = QCheckBox("Auto-discover peers on LAN (mDNS)")
        self.cb_log        = QCheckBox("Write picophone.log file")
        f.addRow("Identity:", self.ed_identity)
        f.addRow("Port:",     self.sb_port)
        f.addRow(self.cb_v6)
        f.addRow(self.cb_autoanswer)
        f.addRow(self.cb_mdns)
        f.addRow(self.cb_log)
        return page

    def _build_audio(self):
        page = QGroupBox()
        f = QFormLayout(page)

        self.cb_in  = QComboBox(); self.cb_in.addItem("default", "default")
        self.cb_out = QComboBox(); self.cb_out.addItem("default", "default")
        for idx, name in _list_devices("input"):
            self.cb_in.addItem(name, idx)
        for idx, name in _list_devices("output"):
            self.cb_out.addItem(name, idx)

        self.cb_rate = QComboBox()
        for r in (8000, 16000, 24000, 48000):
            self.cb_rate.addItem(f"{r} Hz", r)

        self.cb_frame = QComboBox()
        for fr in (10, 20, 40):
            self.cb_frame.addItem(f"{fr} ms", fr)

        self.sb_bitrate = QSpinBox(); self.sb_bitrate.setRange(6_000, 128_000); self.sb_bitrate.setSingleStep(2000)
        self.sb_bitrate.setSuffix(" bps")

        self.cb_aec = QCheckBox("Echo cancellation — classic FDAF + Wiener (fast, ~5 MB)")
        self.cb_dfn = QCheckBox("AI mode — DeepFilterNet3 neural NS + dereverb (heavy, ~150 MB)")
        self.cb_dfn.setToolTip("Replaces the classic AEC with a neural network "
                                "(DeepFilterNet3, same family as Krisp / Skype).\n"
                                "Best on headphones / quiet rooms; doesn't need the "
                                "playback reference signal.\n"
                                "Mutually exclusive with the classic AEC checkbox.")
        self.cb_ns  = QCheckBox("Noise suppression (when AEC backend supports it)")
        self.cb_vad = QCheckBox("Voice activity detection / silence threshold")

        self.sp_thresh = QDoubleSpinBox(); self.sp_thresh.setRange(-90.0, 0.0); self.sp_thresh.setSuffix(" dB")
        self.sp_thresh.setSingleStep(2.0)

        f.addRow("Input device:",   self.cb_in)
        f.addRow("Output device:",  self.cb_out)
        f.addRow("Sample rate:",    self.cb_rate)
        f.addRow("Frame size:",     self.cb_frame)
        f.addRow("Opus bitrate:",   self.sb_bitrate)
        f.addRow(self.cb_aec)
        f.addRow(self.cb_dfn)
        f.addRow(self.cb_ns)
        f.addRow(self.cb_vad)
        # Make AEC and DFN mutually exclusive.
        self.cb_aec.toggled.connect(lambda on: on and self.cb_dfn.setChecked(False))
        self.cb_dfn.toggled.connect(lambda on: on and self.cb_aec.setChecked(False))
        f.addRow("Silence threshold:", self.sp_thresh)
        return page

    def _build_security(self):
        page = QGroupBox()
        f = QFormLayout(page)
        self.cb_encrypt = QCheckBox("Encrypt media with AES-128-GCM (per-call HKDF)")
        self.ed_password = QLineEdit(); self.ed_password.setEchoMode(QLineEdit.Password)
        self.ed_password.setPlaceholderText("Pre-shared key (PSK) — must match on both sides")
        f.addRow(self.cb_encrypt)
        f.addRow("PSK / password:", self.ed_password)
        return page

    # -------- load / save --------

    def _load(self) -> None:
        n, a, u = self.cfg.net, self.cfg.audio, self.cfg.ui  # noqa: F841

        self.ed_identity.setText(n.identity)
        self.sb_port.setValue(n.port)
        self.cb_v6.setChecked(n.bind_v6)
        self.cb_autoanswer.setChecked(n.autoanswer)
        self.cb_mdns.setChecked(n.mdns)
        self.cb_log.setChecked(self.cfg.ui.generate_log)

        self._select(self.cb_in,  a.record_device)
        self._select(self.cb_out, a.play_device)
        self._select(self.cb_rate,  a.sample_rate_hz)
        self._select(self.cb_frame, a.frame_ms)
        self.sb_bitrate.setValue(a.opus_bitrate_bps)
        self.cb_aec.setChecked(a.aec and not a.dfn)
        self.cb_dfn.setChecked(a.dfn)
        self.cb_ns.setChecked(a.ns)
        self.cb_vad.setChecked(a.vad)
        self.sp_thresh.setValue(a.input_threshold_db)

        self.cb_encrypt.setChecked(n.encrypt)
        self.ed_password.setText(n.password)

    @staticmethod
    def _select(combo: QComboBox, value) -> None:
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return
        if isinstance(value, (int, str)):
            combo.addItem(str(value), value)
            combo.setCurrentIndex(combo.count() - 1)

    def _accept(self) -> None:
        n, a = self.cfg.net, self.cfg.audio

        n.identity   = self.ed_identity.text().strip() or "anon"
        n.port       = self.sb_port.value()
        n.bind_v6    = self.cb_v6.isChecked()
        n.autoanswer = self.cb_autoanswer.isChecked()
        n.mdns       = self.cb_mdns.isChecked()

        a.record_device   = self.cb_in.currentData()
        a.play_device     = self.cb_out.currentData()
        a.sample_rate_hz  = int(self.cb_rate.currentData())
        a.frame_ms        = int(self.cb_frame.currentData())
        a.opus_bitrate_bps = int(self.sb_bitrate.value())
        a.aec = self.cb_aec.isChecked()
        a.dfn = self.cb_dfn.isChecked()
        a.ns  = self.cb_ns.isChecked()
        a.vad = self.cb_vad.isChecked()
        a.input_threshold_db = float(self.sp_thresh.value())

        n.encrypt  = self.cb_encrypt.isChecked()
        n.password = self.ed_password.text()

        self.cfg.ui.generate_log = self.cb_log.isChecked()

        self.accept()
