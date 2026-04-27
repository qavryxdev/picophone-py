from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Callable

import numpy as np

from picophone.config import AudioCfg

log = logging.getLogger(__name__)

try:
    import sounddevice as sd
except Exception as e:  # noqa: BLE001
    sd = None
    log.warning("sounddevice unavailable: %s", e)

try:
    import opuslib
except Exception:
    opuslib = None

if opuslib is None:
    # On Windows opuslib uses ctypes.util.find_library('opus') which searches PATH.
    # pyogg ships a bundled opus.dll — prepend its directory and retry.
    try:
        import os
        import pyogg
        os.environ["PATH"] = os.path.dirname(pyogg.__file__) + os.pathsep + os.environ.get("PATH", "")
        import opuslib  # noqa: F401  retry
    except Exception as e:  # noqa: BLE001
        log.warning("opuslib unavailable: %s", e)

try:
    from webrtc_audio_processing import AudioProcessingModule as APM
except Exception:  # noqa: BLE001
    APM = None


PacketCallback = Callable[[bytes], None]


class AudioEngine:
    """Capture -> [AEC/NS] -> Opus encode -> on_packet(); on inbound: enqueue() -> Opus decode -> playback."""

    def __init__(self, cfg: AudioCfg, on_packet: PacketCallback) -> None:
        self.cfg = cfg
        self.on_packet = on_packet
        self.muted = False
        self.tx_rms = 0.0
        self.rx_rms = 0.0
        self._frame_samples = cfg.sample_rate_hz * cfg.frame_ms // 1000
        self._stream_in = None
        self._stream_out = None
        self._enc = None
        self._dec = None
        self._apm = None
        self._jitter: deque[bytes] = deque(maxlen=64)
        self._lock = threading.Lock()
        self._render_ref = np.zeros(self._frame_samples, dtype=np.int16)

    def start(self) -> None:
        if sd is None or opuslib is None:
            raise RuntimeError("sounddevice and opuslib are required")
        rate = self.cfg.sample_rate_hz
        self._enc = opuslib.Encoder(rate, 1, opuslib.APPLICATION_VOIP)
        self._enc.bitrate = self.cfg.opus_bitrate_bps
        self._dec = opuslib.Decoder(rate, 1)
        if APM and self.cfg.aec:
            self._apm = APM(aec_type=2, enable_ns=self.cfg.ns, enable_vad=self.cfg.vad)
            self._apm.set_stream_format(rate, 1)

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
        self._enc = self._dec = self._apm = None

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
        if self._apm is not None:
            pcm = self._apm.process_stream(pcm, self._render_ref)
        self.tx_rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-9) / 32768.0
        if self.muted or self._below_threshold(pcm):
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
            pcm = self._dec.decode(b"", self._frame_samples, decode_fec=False) if self._dec else b"\x00" * frames * 2
        else:
            pcm = self._dec.decode(payload, self._frame_samples, decode_fec=False)
        arr = np.frombuffer(pcm, dtype=np.int16)
        outdata[:, 0] = arr[:frames] if arr.size >= frames else np.pad(arr, (0, frames - arr.size))
        self.rx_rms = float(np.sqrt(np.mean(arr.astype(np.float32) ** 2)) + 1e-9) / 32768.0
        self._render_ref = outdata[:, 0].copy()

    def _below_threshold(self, pcm: np.ndarray) -> bool:
        if not self.cfg.vad:
            return False
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-9)
        db = 20.0 * np.log10(rms / 32768.0)
        return db < self.cfg.input_threshold_db
