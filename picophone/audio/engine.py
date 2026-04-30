from __future__ import annotations

import logging
import math
import sys
import threading
import time
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
        # Optional +10 dB boost toggles (matching original PicoPhone's
        # "10 dB mic gain" / "10 dB spk gain" buttons).
        self.in_boost_db  = float(cfg.in_gain_db)    # 0 or 10
        self.out_boost_db = float(cfg.out_gain_db)
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
        # DFN-output fade-off during prolonged input silence: DFN3 hallucinates
        # band-limited noise on quiet frames; instead of hard-gating it, apply
        # a soft per-frame gain that decays toward 0 after a short grace
        # period and snaps back to 1 immediately on real speech.
        self._silence_count = 0
        self._silence_gain = 1.0
        # Adaptive jitter buffer: track inter-arrival jitter and dynamically
        # size the playback prefill depth.  On a clean LAN target_depth stays
        # near the floor (2 frames = 40 ms); on lossy WiFi/4G it grows to
        # absorb burst arrivals without underflow.
        self._last_arrival_t: float = 0.0
        self.jitter_ms: float = 0.0          # EMA of |inter_arrival - frame_ms|
        self._priming: bool = True           # waiting for buffer to reach target_depth
        self.rtt_ms: float = 0.0             # set externally from PING/PONG (call.py)
        self._packets_received: int = 0
        self._packets_underflowed: int = 0   # rising-edge underflows (drained while in-call)

    def start(self) -> None:
        if sd is None or opuslib is None:
            raise RuntimeError("sounddevice and opuslib are required")
        rate = self.cfg.sample_rate_hz
        self._enc = opuslib.Encoder(rate, 1, opuslib.APPLICATION_VOIP)
        self._enc.bitrate = self.cfg.opus_bitrate_bps
        self._dec = opuslib.Decoder(rate, 1)
        # AEC and DFN3 are independent: AEC removes the speaker -> mic echo
        # path, DFN3 then denoises whatever residue is left.  Either or
        # both may be enabled by config.
        self._aec = make_aec(self._frame_samples, rate,
                             ns=self.cfg.ns, vad=self.cfg.vad) \
                    if self.cfg.aec else NullAec()
        self._post = make_post(self._frame_samples, rate, enable_dfn=self.cfg.dfn)

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
        self._silence_count = 0
        self._silence_gain = 1.0
        # Reset adaptive jitter state so a re-call doesn't carry stats over.
        self._last_arrival_t = 0.0
        self.jitter_ms = 0.0
        self._priming = True
        self.rtt_ms = 0.0
        self._packets_received = 0
        self._packets_underflowed = 0

    @staticmethod
    def _dev(d):
        if isinstance(d, str) and d == "default":
            return None
        return d

    JITTER_EMA_ALPHA = 0.1               # higher = react faster, noisier
    JITTER_DEPTH_MIN = 2
    JITTER_DEPTH_MAX = 15

    def push_packet(self, opus_payload: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            self._jitter.append(opus_payload)
            self._packets_received += 1
            if self._last_arrival_t > 0.0:
                # Deviation of inter-arrival from the expected frame interval.
                dt_ms = (now - self._last_arrival_t) * 1000.0
                deviation = abs(dt_ms - self.cfg.frame_ms)
                self.jitter_ms = (
                    (1.0 - self.JITTER_EMA_ALPHA) * self.jitter_ms
                    + self.JITTER_EMA_ALPHA * deviation
                )
            self._last_arrival_t = now

    @property
    def target_depth(self) -> int:
        """Adaptive prefill depth: covers measured jitter + safety frame."""
        extra = math.ceil(self.jitter_ms / max(1, self.cfg.frame_ms))
        return max(self.JITTER_DEPTH_MIN,
                   min(self.JITTER_DEPTH_MAX, self.JITTER_DEPTH_MIN + extra))

    @property
    def loss_pct(self) -> float:
        """Rough loss/underflow rate over the call lifetime (0..100)."""
        if self._packets_received == 0:
            return 0.0
        return 100.0 * self._packets_underflowed / max(1, self._packets_received)

    SILENCE_GRACE_FRAMES = 5            # ~100 ms at 20 ms framing before fade starts
    SILENCE_FADE_PER_FRAME = 0.05       # gain step per silent frame (full mute in ~400 ms)

    def _on_capture(self, indata, frames, time, status):
        if status:
            log.debug("capture status: %s", status)
        pcm = indata[:, 0].copy()
        if self.muted:
            self.tx_rms = 0.0
            return
        raw_silent = self._below_threshold(pcm)
        with self._lock:
            render = self._render_q.popleft() if self._render_q else self._silent_render
        # AEC must run on EVERY frame (including ones below the input
        # threshold) — speaker -> mic echo is often quieter than the
        # user's own voice but still loud enough for the remote side
        # to hear themselves.  Skipping AEC on silence frames lets that
        # echo straight through.  When AEC is disabled this is a NullAec
        # passthrough.
        pcm = self._aec.process(pcm, render)
        if self._post is not None:
            pcm = self._post.process(pcm)
            if raw_silent:
                self._silence_count += 1
                if self._silence_count > self.SILENCE_GRACE_FRAMES:
                    self._silence_gain = max(0.0, self._silence_gain - self.SILENCE_FADE_PER_FRAME)
            else:
                self._silence_count = 0
                self._silence_gain = 1.0
            if self._silence_gain < 1.0:
                pcm = (pcm.astype(np.float32) * self._silence_gain).astype(np.int16)
        in_factor = self.in_gain * (10.0 ** (self.in_boost_db / 20.0))
        if abs(in_factor - 1.0) > 1e-3:
            scaled = pcm.astype(np.float32) * in_factor
            pcm = np.clip(scaled, -32768, 32767).astype(np.int16)
        self.tx_rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)) + 1e-9) / 32768.0
        try:
            payload = self._enc.encode(pcm.tobytes(), self._frame_samples)
            self.on_packet(payload)
        except Exception:  # noqa: BLE001
            log.exception("encode failed")

    def _on_playback(self, outdata, frames, time, status):
        if status:
            log.debug("playback status: %s", status)
        # Adaptive jitter buffer: while priming, output silence until the
        # buffer reaches target_depth.  This absorbs network jitter without
        # adding fixed latency on clean links.
        with self._lock:
            buffered = len(self._jitter)
            if self._priming:
                if buffered >= self.target_depth:
                    self._priming = False
                    payload = self._jitter.popleft()
                else:
                    payload = None
            else:
                payload = self._jitter.popleft() if buffered > 0 else None
                if payload is None:
                    # Underflow: re-enter priming so the next frames don't
                    # get a half-empty buffer and oscillate.
                    self._priming = True
                    self._packets_underflowed += 1
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
        else:
            out_factor = self.out_gain * (10.0 ** (self.out_boost_db / 20.0))
            if abs(out_factor - 1.0) > 1e-3:
                scaled = arr.astype(np.float32) * out_factor
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
