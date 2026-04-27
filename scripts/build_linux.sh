#!/usr/bin/env bash
# Build single-file PicoPhone-Py binary for Linux via PyInstaller.
# Tested on Debian / Ubuntu. Run from project root or any subdir.
set -euo pipefail

cd "$(dirname "$0")/.."

NAME="PicoPhone-Py"

# --- system deps check -------------------------------------------------------
need_pkg=()
have() { command -v "$1" >/dev/null 2>&1; }

if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then need_pkg+=(libportaudio2); fi
if ! ldconfig -p 2>/dev/null | grep -q libopus.so;   then need_pkg+=(libopus0);     fi
if ! have avahi-daemon;                              then need_pkg+=(avahi-daemon); fi

if [ "${#need_pkg[@]}" -gt 0 ]; then
    echo "Missing system packages: ${need_pkg[*]}"
    echo "On Debian/Ubuntu:  sudo apt install ${need_pkg[*]}"
    if [ "${PICOPHONE_INSTALL_SYSDEPS:-}" = "1" ] && have apt-get; then
        sudo apt-get update -y
        sudo apt-get install -y "${need_pkg[@]}"
    else
        echo "Set PICOPHONE_INSTALL_SYSDEPS=1 to auto-install via apt, or install manually first."
        exit 1
    fi
fi

# --- python deps -------------------------------------------------------------
python3 -m pip install --upgrade pip pyinstaller >/dev/null
python3 -m pip install -e .

# --- build -------------------------------------------------------------------
rm -rf build/ "dist/${NAME}"

python3 -m PyInstaller \
    --noconfirm --windowed --onefile --clean \
    --name "${NAME}" \
    --add-data "picophone/ui/skin.qss:picophone/ui" \
    --collect-submodules zeroconf \
    --collect-submodules cryptography \
    -m picophone

echo
echo "============================================================"
echo "  Built: $(pwd)/dist/${NAME}"
echo "  Run with:  ./dist/${NAME}"
echo "============================================================"
