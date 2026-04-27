"""Strip unused PySide6 / Qt components from a cx_Freeze build.

cx_Freeze copies the entire PySide6 directory tree even when most modules
aren't actually imported.  For PicoPhone-Py we only need:
    QtCore, QtGui, QtWidgets, QtNetwork
…and the corresponding plugins (platforms, imageformats).  Everything else
(3D, Charts, Multimedia, WebEngine, QML, Quick, Bluetooth, …) is dead
weight that bloats the bundle from ~200 MB to ~700 MB.

Usage:  python scripts/prune_dist.py [dist_dir]
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

KEEP_QT_MODULES = {
    "Core", "Gui", "Widgets", "Network",
}

KEEP_PLATFORM_PLUGINS = {"qwindows", "qoffscreen", "qminimal"}
KEEP_PLUGIN_DIRS      = {"platforms", "imageformats", "styles", "tls", "iconengines"}
KEEP_TRANSLATIONS     = {"en"}            # English is the fallback; rest is bloat

# Top-level dirs in PySide6 we don't need at all
DROP_PYSIDE_DIRS = {"qml", "resources", "scripts", "QtAsyncio", "translations"}


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:6.1f} {u}"
        n //= 1024
    return f"{n} TB"


def folder_size(p: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def is_kept_qt_dll(name: str) -> bool:
    """Qt dlls are named Qt6<Module>.dll — keep only allow-listed modules."""
    base = name.lower()
    if not (base.startswith("qt6") and base.endswith(".dll")):
        return True   # not a Qt6 dll — leave alone
    mod = name[3:-4]   # strip "Qt6" prefix and ".dll" suffix
    return mod in KEEP_QT_MODULES


def is_kept_pyside_pyd(name: str) -> bool:
    """PySide6 pyd extensions: Qt<Module>.pyd, plus shiboken6.pyd etc."""
    if not name.endswith(".pyd"):
        return True
    if name.startswith("Qt"):
        mod = name[2:].split(".", 1)[0]
        return mod in KEEP_QT_MODULES
    return True


def prune_pyside(pyside_dir: Path) -> tuple[int, int]:
    before = folder_size(pyside_dir)

    # Drop entire subtrees we know we don't use.
    for d in DROP_PYSIDE_DIRS:
        target = pyside_dir / d
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    # Re-create a tiny translations/ with just English so Qt doesn't whine.
    tr = pyside_dir / "translations"
    tr.mkdir(exist_ok=True)

    # Drop unwanted Qt6*.dll files at the PySide6 root.
    for entry in pyside_dir.iterdir():
        if entry.is_file() and not is_kept_qt_dll(entry.name):
            try:
                entry.unlink()
            except OSError:
                pass
        if entry.is_file() and not is_kept_pyside_pyd(entry.name):
            try:
                entry.unlink()
            except OSError:
                pass

    # Trim plugins/ to only the dirs/plugins we use.
    plugins = pyside_dir / "plugins"
    if plugins.is_dir():
        for sub in plugins.iterdir():
            if sub.is_dir() and sub.name not in KEEP_PLUGIN_DIRS:
                shutil.rmtree(sub, ignore_errors=True)
        platforms = plugins / "platforms"
        if platforms.is_dir():
            for f in platforms.iterdir():
                if f.is_file() and f.stem not in KEEP_PLATFORM_PLUGINS:
                    try:
                        f.unlink()
                    except OSError:
                        pass

    after = folder_size(pyside_dir)
    return before, after


def prune_numpy(numpy_libs_dir: Path) -> tuple[int, int]:
    """numpy.libs/ on Windows ships OpenBLAS (~20 MB).  We keep it — removing
    it makes numpy fall back to slow scalar code or fail to load — but we
    still report its size."""
    if not numpy_libs_dir.exists():
        return 0, 0
    sz = folder_size(numpy_libs_dir)
    return sz, sz


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/PicoPhone-Py").resolve()
    lib  = dist / "lib"
    if not lib.is_dir():
        print(f"FAIL: not a cx_Freeze dist (missing {lib})")
        return 1

    pyside = lib / "PySide6"
    total_before = folder_size(dist)
    print(f"Bundle before:        {human(total_before)}")
    if pyside.is_dir():
        b, a = prune_pyside(pyside)
        print(f"  PySide6:    {human(b)} -> {human(a)}  (saved {human(b - a)})")

    nplibs = lib / "numpy.libs"
    if nplibs.is_dir():
        b, _ = prune_numpy(nplibs)
        print(f"  numpy.libs: {human(b)}  (kept — removing breaks numpy)")

    total_after = folder_size(dist)
    print(f"Bundle after:         {human(total_after)}")
    print(f"Saved:                {human(total_before - total_after)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
