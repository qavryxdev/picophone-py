"""Wrap a pruned cx_Freeze build into a single self-extracting .exe.

Layout:
    [ 7z.sfx ][ config.txt ][ 7z archive of dist/PicoPhone-Py/ ]

When run, the SFX extracts to %TEMP%\\PicoPhone-Py-XXXX\\ and launches
PicoPhone-Py.exe.  No external dependencies, no installer prompts.

Usage:
    python scripts/build_sfx.py [path-to-7z.sfx] [path-to-7zr.exe]
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "PicoPhone-Py"
OUT  = ROOT / "dist" / "PicoPhone-Py-portable.exe"

DEFAULT_SFX = Path("%USERPROFILE%/bin/7z/installer-content/7z.sfx")
DEFAULT_7ZR = Path("%USERPROFILE%/bin/7z/7zr.exe")

# .sfx config — controls extraction behaviour
CONFIG = (
    ";!@Install@!UTF-8!\r\n"
    'Title="PicoPhone-Py"\r\n'
    'BeginPrompt=""\r\n'
    'RunProgram="PicoPhone-Py.exe"\r\n'
    "GUIMode=\"2\"\r\n"             # 2 = silent, no progress dialog
    ";!@InstallEnd@!\r\n"
).encode("utf-8")


def main() -> int:
    sfx_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SFX
    sevenzr  = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_7ZR

    if not DIST.is_dir():
        print(f"FAIL: build first — missing {DIST}")
        return 1
    if not sfx_path.is_file():
        print(f"FAIL: missing 7z.sfx at {sfx_path}")
        return 1
    if not sevenzr.is_file():
        print(f"FAIL: missing 7zr.exe at {sevenzr}")
        return 1

    archive = ROOT / "dist" / "_payload.7z"
    if archive.exists():
        archive.unlink()

    print(f"Compressing {DIST} -> {archive} ...")
    rc = subprocess.run(
        [str(sevenzr), "a", "-t7z", "-mx=7", "-mmt=on", str(archive), "*"],
        cwd=DIST, check=False,
    ).returncode
    if rc != 0:
        print(f"FAIL: 7zr returned {rc}")
        return rc

    print(f"Concatenating sfx + config + archive -> {OUT}")
    with open(OUT, "wb") as out:
        out.write(sfx_path.read_bytes())
        out.write(CONFIG)
        out.write(archive.read_bytes())
    archive.unlink()

    sz = OUT.stat().st_size
    mb = sz / (1024 * 1024)
    print()
    print("=" * 60)
    print(f" Built single-file portable exe:")
    print(f"   {OUT}    ({mb:.1f} MB)")
    print(" Double-click to run; extracts to %TEMP% and launches the GUI.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
