"""Toggle 'start with Windows' via the per-user Run registry key.

Only meaningful for the frozen Nuitka exe — autostart from source mode
(running `python -m picophone`) is unsupported.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REG_NAME = "PicoPhone-Py"
REG_SUB  = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _exe_path() -> str | None:
    """Absolute path to the launched binary, or None if running from source."""
    p = Path(sys.argv[0]).resolve()
    if p.suffix.lower() == ".exe":
        return str(p)
    return None


def is_enabled() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_SUB) as k:
            val, _ = winreg.QueryValueEx(k, REG_NAME)
            return bool(val)
    except (FileNotFoundError, OSError):
        return False


def set_enabled(enable: bool, args: tuple[str, ...] = ("--tray",)) -> bool:
    """Enable/disable. Returns True on success, False if not applicable
    (non-Windows, source mode, registry write failed)."""
    if sys.platform != "win32":
        return False
    exe = _exe_path()
    if not exe:
        return False        # don't autostart a python source script
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_SUB) as k:
            if enable:
                cmd = f'"{exe}"'
                if args:
                    cmd += " " + " ".join(args)
                winreg.SetValueEx(k, REG_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, REG_NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False
