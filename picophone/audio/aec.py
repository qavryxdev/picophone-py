"""Cross-platform acoustic echo cancellation.

Preferred backend: webrtc-audio-processing (AEC3-class echo canceller from
the WebRTC stack) — best quality on real hardware.  When the binding can't
be loaded we fall back to a pure-NumPy NLMS frequency-domain adaptive
filter (FDAF) so the project runs anywhere without compilers.
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)


class Aec(Protocol):
    def process(self, capture_int16: np.ndarray, render_int16: np.ndarray) -> np.ndarray: ...
    def reset(self) -> None: ...


class NullAec:
    def process(self, capture_int16: np.ndarray, _render_int16: np.ndarray) -> np.ndarray:
        return capture_int16
    def reset(self) -> None: ...


class FdafAec:
    """Overlap-save normalised LMS in the frequency domain (FDAF).

    Block size N = frame_samples; FFT size M = 2N. Filter response of length N
    is adapted with a power-normalised gradient. Followed by a simple residual
    echo suppressor (NLP) that attenuates the output when far-end energy
    dominates near-end energy (high ERLE region).
    """

    def __init__(self, frame_samples: int, mu: float = 0.5,
                 reg_load: float = 0.5,
                 dtd_factor: float = 1.5,
                 floor: float = 0.02) -> None:
        self.N = int(frame_samples)
        self.M = 2 * self.N
        self.mu = float(mu)
        self.reg_load  = float(reg_load)
        self.dtd_factor = float(dtd_factor)        # Geigel DTD threshold
        self.floor      = float(floor)             # Wiener gain floor (no full mute)
        self.W      = np.zeros(self.M // 2 + 1, dtype=np.complex64)
        self.x_prev = np.zeros(self.N,             dtype=np.float32)
        self._eps = 1e-6
        self._S    = np.zeros(self.M // 2 + 1, dtype=np.float32)   # smoothed |X|^2
        self._Pe   = np.zeros(self.M // 2 + 1, dtype=np.float32)   # smoothed |Y_hat|^2 (echo)
        self._Pd   = np.zeros(self.M // 2 + 1, dtype=np.float32)   # smoothed |D|^2     (mic)
        self._G    = np.ones (self.M // 2 + 1, dtype=np.float32)   # smoothed gain

    def reset(self) -> None:
        self.W.fill(0)
        self.x_prev.fill(0)
        self._S.fill(0)
        self._Pe.fill(0); self._Pd.fill(0); self._G.fill(1.0)

    def process(self, capture_int16: np.ndarray, render_int16: np.ndarray) -> np.ndarray:
        d = capture_int16.astype(np.float32) / 32768.0
        x = render_int16.astype(np.float32) / 32768.0
        if x.size != self.N or d.size != self.N:
            return capture_int16

        x_block = np.concatenate([self.x_prev, x])
        self.x_prev = x.copy()
        X = np.fft.rfft(x_block, self.M)

        # Echo estimate (frequency + time domain).
        Y = X * self.W
        y_hat = np.fft.irfft(Y, self.M)[self.N:]
        e = d - y_hat

        # ---- Geigel double-talk detector ----
        # Near-end speech is present if the mic envelope exceeds what the
        # echo path could plausibly produce.  When DTD fires we *freeze* the
        # filter (so the adapter doesn't diverge against near-end speech)
        # but we don't suppress the residual — that lets the user be heard.
        max_x = float(np.max(np.abs(x_block)) + self._eps)
        max_d = float(np.max(np.abs(d))       + self._eps)
        near_end_active = (max_d > self.dtd_factor * max_x) and (max_d > 5e-3)

        # ---- Filter adaptation (only during far-end-only) ----
        if not near_end_active:
            e_pad = np.zeros(self.M, dtype=np.float32)
            e_pad[self.N:] = e
            E = np.fft.rfft(e_pad, self.M)

            Sx = (X.real * X.real + X.imag * X.imag).astype(np.float32)
            self._S = 0.85 * self._S + 0.15 * Sx
            S_avg = float(np.mean(self._S))
            denom = self._S + self.reg_load * S_avg + self._eps

            grad = (np.conj(X) * E) / denom
            grad_t = np.fft.irfft(grad, self.M)
            grad_t[self.N:] = 0
            self.W = (self.W + self.mu * np.fft.rfft(grad_t, self.M)).astype(np.complex64)

        # ---- Wiener-style per-bin post-filter ----
        # gain[k] = 1 - |echo[k]|^2 / |mic[k]|^2
        # When echo dominates (far-end-only): ratio -> 1, gain -> 0 (suppress).
        # When near-end dominates (double-talk or near-end-only): ratio < 1,
        # gain -> 1, near-end passes through.  Power estimates are smoothed
        # to avoid musical noise.
        d_pad = np.zeros(self.M, dtype=np.float32)
        d_pad[self.N:] = d
        D = np.fft.rfft(d_pad, self.M)
        echo_p = (Y.real * Y.real + Y.imag * Y.imag).astype(np.float32)
        mic_p  = (D.real * D.real + D.imag * D.imag).astype(np.float32)
        self._Pe = 0.5 * self._Pe + 0.5 * echo_p
        self._Pd = 0.5 * self._Pd + 0.5 * mic_p
        gain = 1.0 - self._Pe / (self._Pd + self._eps)
        np.clip(gain, self.floor, 1.0, out=gain)
        # Asymmetric smoothing: track *down* fast (suppress echo quickly),
        # track *up* fast too (release on near-end speech without delay).
        self._G = 0.3 * self._G + 0.7 * gain.astype(np.float32)

        # Apply spectral gain to the residual.
        e_pad = np.zeros(self.M, dtype=np.float32)
        e_pad[self.N:] = e
        E_post = np.fft.rfft(e_pad, self.M) * self._G
        e_out  = np.fft.irfft(E_post, self.M)[self.N:]

        return (e_out * 32768.0).clip(-32768, 32767).astype(np.int16)


class _WebrtcAec:
    """Wrapper around webrtc-audio-processing's AudioProcessingModule.

    The binding wants 10 ms chunks (480 samples at 48 kHz) fed in lockstep:
    process_reverse_stream(playback) then process_stream(capture).  We split
    the engine's 20 ms frames in half and call the pair twice.

    Reading the binding's C++ source (audio_processing_module.cpp):
        aec_type=1 -> echo_control_mobile()    (AECM, narrowband, robotic)
        aec_type=2 -> echo_cancellation()      (legacy desktop AEC, kLowSuppression)
        aec_type=3 -> AEC3                     (commented out; not actually enabled)
        aec_type=0 -> NO AEC at all            (process_stream still HP-filters)
    So aec_type=2 is the only path that gives real wideband echo cancellation.
    NS is disabled (vendor NS produces vocoder artefacts on libwebrtc 0.3);
    set_aec_level / set_system_delay are NOT called — the binding picks
    kLowSuppression internally and APM's delay estimator does the rest.
    """

    # Envelope-based delay tracker: cross-correlate the magnitude envelope of
    # recent playback against capture to find the actual local round-trip.
    ENV_HZ      = 200          # envelope sample rate (5 ms hop)
    ENV_SECONDS = 1.0          # ring length
    MEAS_FRAMES = 25           # measure every ~500 ms (25 × 20 ms frames)
    SEARCH_MS   = 400          # max delay searched
    WINDOW_MS   = 250          # correlation window length
    MIN_CORR    = 0.30         # below this we trust nothing and keep last value
    MAX_STEP_MS = 20           # rate-limit per-update jump for smoothness

    def __init__(self, frame_samples: int, sample_rate_hz: int, ns: bool, vad: bool) -> None:
        from webrtc_audio_processing import AudioProcessingModule  # type: ignore
        self.apm = AudioProcessingModule(aec_type=2, enable_ns=False, enable_vad=False)
        # set_stream_format signature is (in_rate, in_channels, out_rate, out_channels);
        # without all four the binding leaves the OUTPUT config at the default
        # rate so process_stream returns a buffer of the wrong length.
        self.apm.set_stream_format(sample_rate_hz, 1, sample_rate_hz, 1)
        self.apm.set_reverse_stream_format(sample_rate_hz, 1)
        # process_reverse_stream() pushes self._delay_ms into APM via
        # set_stream_delay_ms each call.  60 ms is just the seed; we
        # refine it from real local echo via _maybe_update_delay().
        self._delay_ms = 60
        self.apm.set_system_delay(self._delay_ms)
        self._chunk = sample_rate_hz // 100              # 10 ms chunk (binding requirement)
        if frame_samples % self._chunk:
            log.warning("WebRTC AEC: frame %d not a multiple of %d (10 ms) — passthrough",
                        frame_samples, self._chunk)
            self._chunk = 0
        self._lock = threading.Lock()
        # Envelope ring buffers used for echo-delay estimation.
        self._env_hop = sample_rate_hz // self.ENV_HZ          # samples per env point
        self._env_len = int(self.ENV_HZ * self.ENV_SECONDS)
        self._capture_env = np.zeros(self._env_len, dtype=np.float32)
        self._render_env  = np.zeros(self._env_len, dtype=np.float32)
        self._env_pos = 0
        self._frames_since_meas = 0

    def process(self, capture_int16: np.ndarray, render_int16: np.ndarray) -> np.ndarray:
        if self._chunk == 0 or capture_int16.size != render_int16.size:
            return capture_int16
        out = np.empty_like(capture_int16)
        for i in range(0, capture_int16.size, self._chunk):
            cap_chunk = capture_int16[i:i + self._chunk]
            ren_chunk = render_int16[i:i + self._chunk]
            # Lockstep per 10 ms chunk: APM expects process_reverse_stream
            # immediately followed by process_stream so the internal delay
            # tracker sees a stable temporal ordering.
            self.apm.process_reverse_stream(ren_chunk.tobytes())
            out_bytes = self.apm.process_stream(cap_chunk.tobytes())
            arr = np.frombuffer(out_bytes, dtype=np.int16)
            if arr.size == self._chunk:
                out[i:i + self._chunk] = arr
            else:
                out[i:i + self._chunk] = cap_chunk
            self._update_envs(cap_chunk, ren_chunk)
        self._frames_since_meas += 1
        if self._frames_since_meas >= self.MEAS_FRAMES:
            self._frames_since_meas = 0
            self._maybe_update_delay()
        return out

    def _update_envs(self, cap_chunk: np.ndarray, ren_chunk: np.ndarray) -> None:
        hop = self._env_hop
        n = cap_chunk.size // hop
        if n == 0:
            return
        cap_f = cap_chunk[: n * hop].astype(np.float32).reshape(n, hop)
        ren_f = ren_chunk[: n * hop].astype(np.float32).reshape(n, hop)
        cap_pts = np.abs(cap_f).mean(axis=1) / 32768.0
        ren_pts = np.abs(ren_f).mean(axis=1) / 32768.0
        for j in range(n):
            slot = self._env_pos % self._env_len
            self._capture_env[slot] = cap_pts[j]
            self._render_env[slot]  = ren_pts[j]
            self._env_pos += 1

    def _maybe_update_delay(self) -> None:
        ren_max = float(self._render_env.max())
        cap_max = float(self._capture_env.max())
        if ren_max < 1e-3:
            log.info("AEC tracker: render silent (peak=%.4f, cap=%.4f) — keeping %d ms",
                     ren_max, cap_max, self._delay_ms)
            return
        win  = int(self.ENV_HZ * self.WINDOW_MS / 1000)        # corr window length (env pts)
        nlag = int(self.ENV_HZ * self.SEARCH_MS / 1000)        # max search range (env pts)
        if self._env_pos < win + nlag or self._env_len < win + nlag:
            return  # not enough history yet
        # Roll buffers so the most recent point sits at index -1.
        idx = self._env_pos % self._env_len
        cap = np.roll(self._capture_env, -idx)
        ren = np.roll(self._render_env,  -idx)
        cap_w = cap[-win:]
        cap_w = cap_w - cap_w.mean()
        cap_norm = float(np.linalg.norm(cap_w)) + 1e-9
        best_score = 0.0
        best_lag = self._delay_ms * self.ENV_HZ // 1000
        for lag in range(2, nlag):
            ren_w = ren[-(win + lag):-lag]
            if ren_w.size != win:
                continue
            ren_w = ren_w - ren_w.mean()
            score = float(np.dot(cap_w, ren_w) / (cap_norm * (np.linalg.norm(ren_w) + 1e-9)))
            if score > best_score:
                best_score = score
                best_lag = lag
        peak_ms = best_lag * 1000 // self.ENV_HZ
        if best_score < self.MIN_CORR:
            log.info("AEC tracker: weak corr=%.2f at %d ms (ren=%.3f cap=%.3f) — keeping %d ms",
                     best_score, peak_ms, ren_max, cap_max, self._delay_ms)
            return
        new_delay = peak_ms                                   # back to ms
        # Rate-limit the jump so AEC adaptive filter has time to follow.
        diff = new_delay - self._delay_ms
        if diff >  self.MAX_STEP_MS: new_delay = self._delay_ms + self.MAX_STEP_MS
        if diff < -self.MAX_STEP_MS: new_delay = self._delay_ms - self.MAX_STEP_MS
        if new_delay != self._delay_ms:
            self._delay_ms = int(new_delay)
            self.apm.set_system_delay(self._delay_ms)
            log.info("AEC: dynamic delay -> %d ms (corr=%.2f)", self._delay_ms, best_score)

    def reset(self) -> None:
        # APM has no public reset; left as a no-op.
        pass


def make_post(frame_samples: int, sample_rate_hz: int, enable_dfn: bool):
    """Return an optional AI post-processor with .process(int16) -> int16, or None.

    Implementation: DeepFilterNet3 inference via onnxruntime + libdf
    (no PyTorch).  See picophone.audio.dfn_onnx for details.
    """
    if not enable_dfn:
        return None
    try:
        from picophone.audio.dfn_onnx import DfnOnnxPostProcessor
        post = DfnOnnxPostProcessor(frame_samples, sample_rate_hz)
        log.info("AI post-processor: DeepFilterNet3 (ONNX runtime, CPU)")
        return post
    except Exception as e:  # noqa: BLE001
        log.warning("DeepFilterNet unavailable (%s); skipping AI post", e)
        return None


def make_aec(frame_samples: int, sample_rate_hz: int, ns: bool = True, vad: bool = True,
             prefer_webrtc: bool = True) -> Aec:
    if prefer_webrtc:
        try:
            ec = _WebrtcAec(frame_samples, sample_rate_hz, ns, vad)
            log.info("AEC: WebRTC AEC3 (webrtc-audio-processing)")
            return ec
        except Exception as e:  # noqa: BLE001
            log.info("AEC: WebRTC binding unavailable (%s); falling back to FDAF", e)
    log.info("AEC: NumPy FDAF fallback (block=%d, fs=%d Hz)", frame_samples, sample_rate_hz)
    return FdafAec(frame_samples)
