#!/usr/bin/env bash
# Build a true single-file PicoPhone-Py executable for Mageia Linux.
#
# Works on Mageia 9, Mageia 10/cauldron, and binary-compatible distros
# (Mandriva descendants, openMandriva).  Produces an ELF that's linked
# against the system glibc; for Mageia 9 that's glibc 2.36, so the exe
# also runs on any newer glibc (Mageia 10+, Fedora 39+, RHEL 9+).
#
# Run on a Mageia machine, in a Mageia VM, or inside a Mageia container:
#   docker run --rm -v $PWD:/src -w /src mageia:9 scripts/build_mageia.sh
#
# Output: dist/nuitka/PicoPhone-Py   (one file, ~45 MB)
set -euo pipefail

cd "$(dirname "$0")/.."

# ---------- 0. detect distro ------------------------------------------------
if ! grep -qi mageia /etc/os-release 2>/dev/null; then
    echo "Warning: this script is tuned for Mageia.  Trying to continue anyway..."
fi

# ---------- 1. install system deps (urpmi or dnf) ---------------------------
PACKAGES=(
    python3 python3-pip python3-devel
    gcc gcc-c++ make patchelf chrpath
    portaudio libopus-devel libffi-devel
    fontconfig dbus-libs libxcb1 libX11_6
)
if command -v urpmi >/dev/null 2>&1; then
    INSTALL="urpmi --auto"
elif command -v dnf >/dev/null 2>&1; then
    INSTALL="dnf install -y"
else
    echo "ERROR: neither urpmi nor dnf found — install package manager first."
    exit 1
fi

echo "=== installing system deps via ${INSTALL%% *} ============================"
sudo $INSTALL "${PACKAGES[@]}" || {
    echo "Some packages failed to install — continuing.  If the build later"
    echo "complains about missing libraries, install them manually."
}

# ---------- 2. set up Python venv ------------------------------------------
PY=python3
$PY -m venv .venv-build
# shellcheck disable=SC1091
source .venv-build/bin/activate
python -m pip install --upgrade pip wheel >/dev/null

echo "=== installing Python deps =============================================="
pip install --quiet \
    nuitka ordered-set zstandard \
    PySide6 sounddevice numpy opuslib cryptography tomli-w

# ---------- 3. Nuitka onefile build ----------------------------------------
echo "=== Nuitka onefile build (5-15 min on first run) ========================"
rm -rf dist/nuitka

python -m nuitka \
    --onefile \
    --assume-yes-for-downloads \
    --enable-plugin=pyside6 \
    --include-data-files=picophone/ui/skin.qss=picophone/ui/skin.qss \
    --include-data-files=assets/icons/picophone.ico=assets/icons/picophone.ico \
    --include-package=picophone \
    --include-package=cryptography \
    --include-package=opuslib \
    --include-package=numpy \
    --linux-icon=assets/icons/picophone.ico \
    --output-dir=dist/nuitka \
    --output-filename=PicoPhone-Py \
    picophone/__main__.py

# ---------- 4. report ------------------------------------------------------
EXE="dist/nuitka/PicoPhone-Py"
test -x "$EXE" || { echo "ERROR: build did not produce $EXE"; exit 1; }
size_mb=$(du -m "$EXE" | cut -f1)

echo
echo "============================================================"
echo " Built true single-file Mageia binary:"
echo "   $EXE   (${size_mb} MB)"
echo " glibc requirement: $(ldd --version | head -1)"
echo " Run with:  ./$EXE"
echo "============================================================"

deactivate
