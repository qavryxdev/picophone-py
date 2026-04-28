from __future__ import annotations

import logging
import sys
import threading
from collections import deque
from typing import Callable

import numpy as np

from picophone.audio.aec import Aec, NullAec, make_aec, make_post
from picophone.config import AudioCfg

log = logging.getLogger(__name__)

try:
    import sounddevice as sd
except Exception as e:  # noqa: BLE001
    sd = None
    log.warning("sounddevice unavailable: %s "
                "(Linux: apt install libportaudio2)", e)

try:
    import opuslib
except Exception:
    opuslib = None

if opuslib is None and sys.platform == "win32":
    import os
    candidates = []
    # PyInstaller bundle (one-folder or one-file): _MEIPASS holds extracted resources.
    if hasattr(sys, "_MEIPASS"):
        candidates.append(sys._MEIPASS)  # type: ignore[attr-defined]
    try:
        import pyogg
        candidates.append(os.path.dirname(pyogg.__file__))
    except ImportError:
        pass
    for d in candidates:
        if d and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
    try:
        import opuslib  # noqa: F401  retry
    except Exception as e:  # noqa: BLE001
        log.warning("opuslib unavailable: %s (install pyogg or libopus)", e)
elif opuslib is None:
    log.warning("opuslib unavailable; install libopus (Linux: apt install libopus0; "
                "macOS: brew install opus)")


PacketCallback = Callable[[bytes], None]


class AudioEngine:
    """Capture -> AEC -> Opus encode -> on_packet(); on inbound: enqueue() -> Opus decode -> playback.

    The AEC reference is fed from a small bounded queue of recent playback frames so
    capture and playback callbacks (on different PortAudio threads) stay loosely aligned.
    """

    def __init__(self, cfg: AudioCfg, on_packet: PacketCallback) -> None:
        self.cfg = cfg
        self.on_packet = on_packet
        self.muted = False
        self.spk_muted = False
        # Linear gain factors. Sliders 0..1000 -> 0..1.0 (attenuation only).
        # Slider at 1000 = unity gain, matching the original PicoPhone behaviour
        # where the level controls drove the system mixer (max position = 100%).
        # Going above 1.0 saturates and breaks the AEC's linearity assumption.
        self.in_gain  = max(0.0, min(1.0, cfg.rec_level   / 1000.0))
        self.out_gain = max(0.0, min(1.0, cfg.play_volume / 1000.0))
        self.tx_rms = 0.0
        self.rx_rms = 0.0
        self._frame_samples = cfg.sample_rate_hz * cfg.frame_ms // 1000
        self._stream_in = None
        self._stream_out = None
        self._enc = None
        self._dec = None
        self._aec: Aec = NullAec()
        self._post = None             # neural post-processor (DFN), exclusive with AEC
        self._jitter: deque[bytes] = deque(maxlen=64)
        self._jitter_misses = 0          # consecutive empty popleft attempts
        self._render_q: deque[np.ndarray] = deque(maxlen=8)   # ~160ms at 20ms frames
        self._lock = threading.Lock()
        self._silent_render = np.zeros(self._frame_samples, dtype=np.int16)

    def start(self) -> None:
        if sd is None or opuslib is None:
            raise RuntimeError("sounddevice and opuslib are required")
        rate = self.cfg.sample_rate_hz
        self._enc = opuslib.Encoder(rate, 1, opuslib.APPLICATION_VOIP)
        self._enc.bitrate = self.cfg.opus_bitrate_bps
        self._dec = opuslib.Decoder(rate, 1)
        # AI mode (DFN) and classic mode (FDAF) are mutually exclusive: when
        # DFN is on, FDAF is bypassed to avoid two stages fighting over the
        # same residual.  DFN handles noise + dereverb on its own.
        if self.cfg.dfn:
            self._aec  = NullAec()
            self._post = make_post(self._frame_samples, rate, enable_dfn=True)
            if self._post is None:
                # Fallback: DFN failed to load; use classic FDAF instead.
                self._aec = make_aec(self._frame_samples, rate,
                                     ns=self.cfg.ns, vad=self.cfg.vad) \
                            if self.cfg.aec else NullAec()
        else:
            self._aec  = make_aec(self._frame_samples, rate,
                                  ns=self.cfg.ns, vad=self.cfg.vad) \
                        if self.cfg.aec else NullAec()
            self._post = None

        self._stream_in = sd.InputStream(
            samplerate=rate, channels=1, dtype="int16",
            blocksize=self._frame_samples, callback=self._on_capture,
            device=self._dev(self.cfg.record_device),
        )
        self._stream_out = sd.OutputStream(
            samplerate=rate, channels=1, dtype="int16",
            blocksize=self._frame_samples, callback=self._on_playback,
            device=self._dev(self.cfg.play_device),
        )
        self._stream_in.start()
        self._stream_out.start()

    def stop(self) -> None:
        for s in (self._stream_in, self._stream_out):
            if s:
                s.stop(); s.close()
        self._stream_in = self._stream_out = None
        self._enc = self._dec = None
        self._aec = NullAec()
        self._post = None
        self._render_q.clear()

    @staticmethod
    def _dev(d):
        if isinstance(d, str) and d == "default":
            return None
        return d

    def push_packet(self, opus_payload: bytes) -> None:
        with self._lock:
            self._jitter.append(opus_payload)

    def _on_capture(self, indata, frames, time, status):
        if status:
            log.debug("capture status: %s", status)
        pcm = indata[:, 0].copy()
        # Pre-AEC silence check: if the raw mic frame is below VAD threshold,
        # skip the post-processor entirely.  DFN3 has a tendency to
        # hallucinate band-limited noise when fed near-silence, and that
        # noise can clear the post-processed VAD threshold and end up on
        # the wire.  FDAF doesn't hallucinate but skipping it on silence
        # saves CPU.
        if self.muted or self._below_threshold(pcm):
            self.tx_rms = 0.0
            return
        with self._lock:
            render = self._render_q.popleft() if self._render_q else self._silent_render
        # Either FDAF (classic) or DFN (AI) — never both.
        if self._post is not None:
            pcm = self._post.process(pcm)
        else:
            pcm = self._aec.process(pcm, render)
        if self.in_gain != 1.0:
            scaled = pcm.astype(np.float32) * self.in_gain
            pcm = np.clip(scaled, -32768, 32767).astype(np.int16)
        self.tx_rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-9) / 32768.0
        # Recheck threshold after AEC: linear AEC may have removed echo
        # bringing residual below floor; skip those too.
        if self._below_threshold(pcm):
            return
        try:
            payload = self._enc.encode(pcm.tobytes(), self._frame_samples)
            self.on_packet(payload)
        except Exception:  # noqa: BLE001
            log.exception("encode failed")

    def _on_playback(self, outdata, frames, time, status):
        if status:
            log.debug("playback status: %s", status)
        with self._lock:
            payload = self._jitter.popleft() if self._jitter else None
        if payload is None:
            self._jitter_misses += 1
            # Opus PLC is intended to bridge SHORT (1-3 frame) packet losses.
            # Once the peer goes silent for longer (their VAD dropped frames,
            # they're not speaking) PLC drifts and produces audible noise on
            # our speaker.  After 3 consecutive misses, output real zeros
            # instead of letting PLC hallucinate.
            if self._jitter_misses > 3 or self._dec is None:
                arr = np.zeros(frames, dtype=np.int16)
                outdata[:, 0] = arr
                self.rx_rms = 0.0
                with self._lock:
                    self._render_q.append(arr.copy())
                return
            pcm = self._dec.decode(b"", self._frame_samples, decode_fec=False)
        else:
            self._jitter_misses = 0
            pcm = self._dec.decode(payload, self._frame_samples, decode_fec=False)
        arr = np.frombuffer(pcm, dtype=np.int16)
        if arr.size < frames:
            arr = np.pad(arr, (0, frames - arr.size))
        else:
            arr = arr[:frames]
        if self.spk_muted:
            arr = np.zeros(frames, dtype=np.int16)
        elif self.out_gain != 1.0:
            scaled = arr.astype(np.float32) * self.out_gain
            arr = np.clip(scaled, -32768, 32767).astype(np.int16)
        outdata[:, 0] = arr
        self.rx_rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)) + 1e-9) / 32768.0
        with self._lock:
            self._render_q.append(arr.copy())

    def _below_threshold(self, pcm: np.ndarray) -> bool:
        if not self.cfg.vad:
            return False
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-9)
        db = 20.0 * np.log10(rms / 32768.0)
        return db < self.cfg.input_threshold_db
