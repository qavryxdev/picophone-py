# PicoPhone-Py

Modern multiplatform reimplementation of [PicoPhone](http://www.vitez.it/picophone/) (Javier Aldazabal, 2009).
Same lightweight feel, but with HD audio, AEC, IPv6 and end-to-end crypto.

## Stack

| Layer        | Choice                                    |
|--------------|-------------------------------------------|
| GUI          | PySide6 (Qt 6) — frameless, custom QSS skin |
| Audio I/O    | `sounddevice` (PortAudio: WASAPI / CoreAudio / ALSA) |
| Codec        | Opus 48 kHz mono via `opuslib`            |
| AEC / NS     | `webrtc-audio-processing` (AEC3)          |
| Signaling    | JSON-over-UDP, dual-stack IPv4 + IPv6     |
| Media        | RTP-like header, AES-128-GCM payload      |
| Discovery    | mDNS / Zeroconf (`_picophonepy._udp`)     |
| Config       | TOML (`~/.picophone/picophone.toml`), auto-migrates legacy `PicoPhone.ini` |

## Run from source

```bash
pip install -e .[aec,vad,dev]
python -m picophone
```

`webrtc-audio-processing` may need a C++ compiler / `cmake` on Windows.
If unavailable, AEC stays off but the rest works.

## Build single-file binary

```bash
pyinstaller --noconfirm --windowed --name PicoPhone-Py \
    --add-data "picophone/ui/skin.qss:picophone/ui" \
    -m picophone
```

## Status

Skeleton — GUI launches, config migrates, signaling/media open dual-stack
sockets, audio engine wires Opus + (optional) AEC3. Call setup glue between
GUI ↔ signaling ↔ media is the next step (MVP).
