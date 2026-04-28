#!/usr/bin/env bash
# Build a single-file PicoPhone-Py executable for Mageia Linux via PyInstaller.
#
# Works on Mageia 9, Mageia 10/cauldron, and binary-compatible distros
# (Mandriva descendants, openMandriva).  Produces an ELF that's linked
# against the system glibc; for Mageia 9 that's glibc 2.36, so the exe
# also runs on any newer glibc (Mageia 10+, Fedora 39+, RHEL 9+).
#
# Run on a Mageia machine, in a Mageia VM, or under WSL.
#
# Output: dist/PicoPhone-Py   (~140 MB single file, no UPX)
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
                       -cf - . ) | ( cd "$WORK" && tar -xf - )
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
    pyinstaller \
    PySide6 sounddevice numpy opuslib cryptography tomli-w onnxruntime
pip install --quiet deepfilterlib 2>/dev/null || true

# ---------- 3. PyInstaller onefile build -----------------------------------
echo "=== PyInstaller onefile build ==========================================="
rm -rf dist build PicoPhone-Py.spec

# Bundle DFN3 AI runtime (ONNX, no torch) if libdf + onnxruntime are
# importable and the model files are in assets/dfn3/.
DFN_FLAGS=()
if python -c "import libdf, onnxruntime" >/dev/null 2>&1 && [ -f assets/dfn3/enc.onnx ]; then
    echo "Bundling DeepFilterNet3 (ONNX, no torch) into the binary."
    # Collect ORT binaries+data only, not every submodule — see Windows
    # build comment for why (transformers/training/tools/quantization
    # add ~1 minute of analysis we never use at runtime).
    DFN_FLAGS=(
        --collect-all libdf
        --collect-binaries onnxruntime
        --collect-data onnxruntime
        --add-data "assets/dfn3:assets/dfn3"
    )
fi

# Parallel bytecode pre-compile so PyInstaller's serial freeze step gets
# cache hits.  -j 0 = all available cores.
python -m compileall -j 0 -q picophone

python -m PyInstaller \
    --onefile \
    --noconsole \
    --noupx \
    --name PicoPhone-Py \
    --icon assets/icons/picophone.ico \
    --add-data "picophone/ui/skin.qss:picophone/ui" \
    --add-data "assets/icons/picophone.ico:assets/icons" \
    --add-data "assets/ringin.wav:assets" \
    --hidden-import picophone.autostart \
    --hidden-import picophone.audio.dfn_onnx \
    --collect-submodules picophone \
    "${DFN_FLAGS[@]}" \
    --distpath dist \
    --workpath build \
    picophone/__main__.py

# ---------- 4. report ------------------------------------------------------
EXE="dist/PicoPhone-Py"
test -x "$EXE" || { echo "ERROR: build did not produce $EXE"; exit 1; }
size_mb=$(du -m "$EXE" | cut -f1)

# If we built in $HOME/build, copy the artifact back to the source tree so the
# user finds it where they expect.
if [[ "$SRC" != "$(pwd)" ]]; then
    mkdir -p "$SRC/dist"
    cp -v "$EXE" "$SRC/dist/PicoPhone-Py"
    EXE="$SRC/dist/PicoPhone-Py"
fi

echo
echo "============================================================"
echo " Built single-file Mageia binary:"
echo "   $EXE   (${size_mb} MB)"
echo " glibc requirement: $(ldd --version | head -1)"
echo " Run with:  ./$(basename "$EXE")"
echo "============================================================"

deactivate
