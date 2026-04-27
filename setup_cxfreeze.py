"""cx_Freeze build spec for PicoPhone-Py.

Run via: python setup_cxfreeze.py build_exe   (or scripts/build_windows.bat)

Produces dist\\PicoPhone-Py\\PicoPhone-Py.exe + sibling DLLs/resources.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from cx_Freeze import Executable, setup

ROOT = Path(__file__).resolve().parent

include_files: list[tuple[str, str]] = [
    (str(ROOT / "picophone" / "ui" / "skin.qss"), "lib/picophone/ui/skin.qss"),
]

# Bundle pyogg's opus.dll next to the main exe so audio works on a clean
# Windows machine without libopus installed.
try:
    import pyogg
    pyogg_dir = Path(pyogg.__file__).parent
    for dll in ("opus.dll",):
        src = pyogg_dir / dll
        if src.exists():
            include_files.append((str(src), dll))
except ImportError:
    pass

build_exe_options = {
    "build_exe": "dist/PicoPhone-Py",
    "packages": [
        "picophone",
        "PySide6",
        "shiboken6",
        "sounddevice",
        "numpy",
        "opuslib",
        "cryptography",
        "zeroconf",
        "tomli_w",
    ],
    "includes": [
        "picophone",
        "picophone.audio",
        "picophone.audio.engine",
        "picophone.audio.aec",
        "picophone.net",
        "picophone.net.signaling",
        "picophone.net.media",
        "picophone.net.discovery",
        "picophone.ui",
        "picophone.ui.main_window",
        "picophone.ui.chat_window",
        "picophone.ui.prefs_dialog",
        "picophone.config",
        "picophone.crypto",
        "picophone.call",
        "picophone.log",
    ],
    "excludes": [
        "tkinter", "test", "unittest", "pydoc_data",
        "PySide6.Qt3D", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtPositioning",
        "PySide6.QtMultimedia", "PySide6.QtWebEngine", "PySide6.QtWebChannel",
        "PySide6.QtWebSockets", "PySide6.QtPdf", "PySide6.QtSql",
    ],
    "include_files": include_files,
    "optimize": 1,
}

base = "gui" if sys.platform == "win32" else None

setup(
    name="PicoPhone-Py",
    version="0.1.0",
    description="Modern multiplatform reimplementation of PicoPhone",
    options={"build_exe": build_exe_options},
    executables=[
        Executable(
            script="picophone/__main__.py",
            base=base,
            target_name="PicoPhone-Py.exe" if sys.platform == "win32" else "PicoPhone-Py",
        ),
    ],
)
