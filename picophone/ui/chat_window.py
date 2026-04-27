from __future__ import annotations

import time
from typing import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout, QWidget,
)


class ChatWindow(QDialog):
    """Persistent chat window with one peer (keyed by identity)."""

    send_clicked = Signal(str, str)   # peer_target, text

    def __init__(self, peer_identity: str, peer_target: str,
                 self_identity: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Chat with {peer_identity}")
        self.resize(380, 320)
        self.peer_identity = peer_identity
        self.peer_target = peer_target
        self.self_identity = self_identity

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)

        self.lbl_target = QLabel(f"{peer_identity}  →  {peer_target}")
        layout.addWidget(self.lbl_target)

        self.log = QTextEdit(readOnly=True)
        layout.addWidget(self.log, 1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Type and press Enter")
        self.btn_send = QPushButton("Send")
        row.addWidget(self.input, 1)
        row.addWidget(self.btn_send)
        layout.addLayout(row)

        self.btn_send.clicked.connect(self._on_send)
        self.input.returnPressed.connect(self._on_send)

    def append(self, sender: str, text: str) -> None:
        ts = time.strftime("%H:%M:%S")
        safe = text.replace("<", "&lt;").replace(">", "&gt;")
        self.log.append(f'<span style="color:#888">[{ts}]</span> '
                        f'<b>{sender}:</b> {safe}')

    def update_target(self, target: str) -> None:
        self.peer_target = target
        self.lbl_target.setText(f"{self.peer_identity}  →  {target}")

    def _on_send(self) -> None:
        text = self.input.text().strip()
        if not text:
            return
        self.append(self.self_identity, text)
        self.send_clicked.emit(self.peer_target, text)
        self.input.clear()

    def keyPressEvent(self, e: QKeyEvent) -> None:
        if e.key() == Qt.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)
