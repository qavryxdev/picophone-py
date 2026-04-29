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

    def __init__(self, frame_samples: int, sample_rate_hz: int, ns: bool, vad: bool) -> None:
        from webrtc_audio_processing import AudioProcessingModule  # type: ignore
        self.apm = AudioProcessingModule(aec_type=2, enable_ns=False, enable_vad=False)
        # set_stream_format signature is (in_rate, in_channels, out_rate, out_channels);
        # without all four the binding leaves the OUTPUT config at the default
        # rate (which differs from in_rate), so process_stream returns a buffer
        # of the wrong length and the audio comes out as garbage.
        self.apm.set_stream_format(sample_rate_hz, 1, sample_rate_hz, 1)
        self.apm.set_reverse_stream_format(sample_rate_hz, 1)
        # process_reverse_stream() calls ap->set_stream_delay_ms(system_delay)
        # internally, so without this APM thinks playback==capture (delay 0)
        # and the AEC adaptive filter looks for echo at the wrong tap →
        # wideband distortion.  60 ms is a realistic OS-level round-trip
        # for default PortAudio buffers; the AEC still tracks small drifts
        # adaptively from there.
        self.apm.set_system_delay(60)
        self._chunk = sample_rate_hz // 100              # 10 ms chunk (binding requirement)
        if frame_samples % self._chunk:
            log.warning("WebRTC AEC: frame %d not a multiple of %d (10 ms) — passthrough",
                        frame_samples, self._chunk)
            self._chunk = 0
        self._lock = threading.Lock()

    def process(self, capture_int16: np.ndarray, render_int16: np.ndarray) -> np.ndarray:
        if self._chunk == 0 or capture_int16.size != render_int16.size:
            return capture_int16
        out = np.empty_like(capture_int16)
        for i in range(0, capture_int16.size, self._chunk):
            # Lockstep per 10 ms chunk: APM expects process_reverse_stream
            # immediately followed by process_stream so the internal delay
            # tracker sees a stable temporal ordering.  Calling them from
            # different threads (one per audio callback) confused APM into
            # frame-level gating, which the user heard as "fast interrupted".
            self.apm.process_reverse_stream(render_int16[i:i + self._chunk].tobytes())
            out_bytes = self.apm.process_stream(capture_int16[i:i + self._chunk].tobytes())
            arr = np.frombuffer(out_bytes, dtype=np.int16)
            if arr.size == self._chunk:
                out[i:i + self._chunk] = arr
            else:
                out[i:i + self._chunk] = capture_int16[i:i + self._chunk]
        return out

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
