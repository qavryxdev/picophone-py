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

SRC="$(cd "$(dirname "$0")/.." && pwd)"

# Building on /mnt/c/... under WSL is ~10x slower than native ext4 because every
# file op crosses the Plan 9 bridge.  When invoked from /mnt/c we mirror the
# project to a native location with tar (no rsync dep), build there, and copy
# the final ELF back.  Native invocation builds in place.
if [[ "$SRC" == /mnt/c/* ]]; then
    WORK="$HOME/build/picophone-py"
    echo "=== mirroring $SRC -> $WORK (native ext4) ==="
    rm -rf "$WORK"
    mkdir -p "$WORK"
    ( cd "$SRC" && tar --exclude=.git --exclude=dist --exclude=build \
                       --exclude=.venv-build --exclude=__pycache__ \
                       --exclude=nuitka-build -cf - . ) | ( cd "$WORK" && tar -xf - )
    cd "$WORK"
else
    cd "$SRC"
fi

# ---------- 0. detect distro ------------------------------------------------
if ! grep -qi mageia /etc/os-release 2>/dev/null; then
    echo "Warning: this script is tuned for Mageia.  Trying to continue anyway..."
fi

# ---------- 1. install system deps (urpmi or dnf) ---------------------------
PACKAGES=(
    python3 python3-pip python3-devel
    gcc gcc-c++ make patchelf chrpath sudo
    lib64opus0 lib64opus-devel
    lib64portaudio2 lib64portaudio-devel
    lib64ffi-devel
    fontconfig lib64xcb1 lib64x11_6
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
JOBS=$(nproc 2>/dev/null || echo 4)
echo "Using $JOBS parallel jobs"

python -m nuitka \
    --onefile \
    --jobs="$JOBS" \
    --assume-yes-for-downloads \
    --enable-plugin=pyside6 \
    --include-data-files=picophone/ui/skin.qss=picophone/ui/skin.qss \
    --include-data-files=assets/icons/picophone.ico=assets/icons/picophone.ico \
    --include-data-files=assets/ringin.wav=assets/ringin.wav \
    --include-package=picophone \
    --include-module=picophone.autostart \
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

# If we built in $HOME/build, copy the artifact back to the source tree so the
# user finds it where they expect.
if [[ "$SRC" != "$(pwd)" ]]; then
    mkdir -p "$SRC/dist/nuitka"
    cp -v "$EXE" "$SRC/dist/nuitka/PicoPhone-Py"
    EXE="$SRC/dist/nuitka/PicoPhone-Py"
fi

echo
echo "============================================================"
echo " Built true single-file Mageia binary:"
echo "   $EXE   (${size_mb} MB)"
echo " glibc requirement: $(ldd --version | head -1)"
echo " Run with:  ./$(basename "$EXE")"
echo "============================================================"

deactivate
