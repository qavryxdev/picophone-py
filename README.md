# PicoPhone-Py

Modern multiplatform reimplementation of [PicoPhone](http://www.vitez.it/picophone/)
(Javier Aldazabal, 2009). Same lightweight feel and skin, but with HD audio,
echo cancellation, IPv6 and end-to-end media encryption. Pure Python — runs on
**Windows, Linux and macOS** without compilers.

## Stack

| Layer        | Choice                                                     |
|--------------|------------------------------------------------------------|
| GUI          | PySide6 (Qt 6) — frameless, custom QSS skin                 |
| Audio I/O    | `sounddevice` (PortAudio: WASAPI / CoreAudio / ALSA / PulseAudio / PipeWire) |
| Codec        | Opus 48 kHz mono via `opuslib` (libopus)                    |
| AEC          | NumPy FDAF (frequency-domain adaptive filter, ~40 dB ERLE) — no native deps. Optional `webrtc-audio-processing` (AEC3) when present. |
| Signaling    | JSON-over-UDP, dual-stack IPv4 + IPv6                       |
| Media        | RTP-like header + AES-128-GCM payload                       |
| Key exchange | HKDF-SHA256(PSK, nonce_a‖nonce_b) per call                  |
| Discovery    | mDNS / Zeroconf (`_picophonepy._udp`) — auto-fills contacts |
| Config       | TOML (`~/.picophone/picophone.toml`); auto-migrates legacy `PicoPhone.ini` |

## Install

### Windows

```powershell
pip install -e .
python -m picophone
```

`pyogg` is automatically pulled (Windows-only dep) and ships a bundled `opus.dll`
that the engine loads at startup.

### Linux (Debian / Ubuntu)

```bash
sudo apt install libportaudio2 libopus0 avahi-daemon
pip install -e .
python -m picophone
```

- `libportaudio2` for `sounddevice`
- `libopus0` for `opuslib`
- `avahi-daemon` so mDNS auto-discovery works (zeroconf can also work without it,
  but Avahi gives reliable discovery between PicoPhone-Py instances)

For optional WebRTC AEC3 (better than the bundled FDAF):
```bash
sudo apt install libwebrtc-audio-processing-dev
pip install webrtc-audio-processing
```

### macOS

```bash
brew install portaudio opus
pip install -e .
python -m picophone
```

## Run

```bash
python -m picophone
```

Two instances on the same machine (test it without two computers):

```bash
# terminal 1 — uses default port 11676
python -m picophone

# terminal 2 — set HOME or edit ~/.picophone/picophone.toml first:
#   identity="bob"  port=11686
python -m picophone
```

Both instances will discover each other via mDNS and the contact combo box
fills automatically. Click **CALL** on one of them. If both have the same
`password` set, media is end-to-end encrypted (AES-128-GCM with per-call key).

## Tests

Six self-contained smoke tests cover the critical paths:

```bash
python scripts/smoke_keyx.py            # HKDF + AES-GCM
python scripts/smoke_signaling.py       # INVITE/ACCEPT/BYE on ::1
python scripts/smoke_media.py           # 50 encrypted RTP-like packets
python scripts/smoke_discovery.py       # mDNS roundtrip
python scripts/smoke_aec.py             # FDAF echo cancellation (>=15 dB ERLE)
python scripts/smoke_call_localhost.py  # full controller wiring
python scripts/smoke_gui.py             # headless Qt launch (offscreen)
```

## Build single-file binary

### Windows — portable Python distribution

```cmd
scripts\build_windows.bat
```

Produces `dist\PicoPhone-Py\` containing:

```
PicoPhone-Py.bat            ← double-click to run
PicoPhone-Py-debug.bat      ← run with console attached
python\python.exe           ← official, signed by Python Software Foundation
python\Lib\site-packages\   ← all deps (PySide6, opus.dll via pyogg, …)
picophone\                  ← our source
assets\
```

Distribute by zipping the entire `dist\PicoPhone-Py\` folder (~700 MB
expanded, ~250 MB zipped — Qt is the bulk).

**Why not PyInstaller?** PyInstaller produces a self-extracting bootloader
that Avast / AVG / Defender flag as `Win64:Malware-gen` (false positive,
shared signature with some malware packers). Bundling python.org's signed
embeddable Python instead avoids the AV trigger entirely — the launcher is
a `.bat` that just runs `python.exe -m picophone`, and `python.exe` is
already trusted because PSF signs it.

### Linux
```bash
PICOPHONE_INSTALL_SYSDEPS=1 scripts/build_linux.sh
```
(Or pre-install `libportaudio2 libopus0 avahi-daemon` and drop the env var.)
Produces `dist/PicoPhone-Py` (one-file ELF). `libopus.so.0` is loaded from the
system at runtime so it stays small.

## Status

- **Working:** dual-stack IPv6 signaling, encrypted media, mDNS auto-discovery,
  Opus codec, FDAF echo cancel (~40 dB ERLE on synthetic linear echo).
- **Planned:** in-call chat (the `MSG`/`CHAT` buttons), preferences dialog
  behind `PREF`, WebRTC AEC3 wheel for Windows, optional speexdsp NS as
  middle-ground noise suppression.
