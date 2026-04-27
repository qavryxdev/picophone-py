"""Cross-platform acoustic echo cancellation.

Default implementation: NLMS frequency-domain adaptive filter (FDAF) using
overlap-save, vectorised in NumPy. No native dependencies — works on Windows,
Linux, and macOS without compilers.

If `webrtc_audio_processing` is installed (Linux: `pip install
webrtc-audio-processing` after `apt install libwebrtc-audio-processing-dev`)
we transparently swap to AEC3 which is significantly better, but the project
is designed to work fine without it.
"""
from __future__ import annotations

import logging
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
    """Wrapper around webrtc-audio-processing's AudioProcessingModule (AEC3 + NS + VAD).
    Used only when the optional binding is importable."""

    def __init__(self, frame_samples: int, sample_rate_hz: int, ns: bool, vad: bool) -> None:
        from webrtc_audio_processing import AudioProcessingModule  # type: ignore
        self.apm = AudioProcessingModule(aec_type=2, enable_ns=ns, enable_vad=vad)
        self.apm.set_stream_format(sample_rate_hz, 1)
        self.frame = frame_samples

    def process(self, capture_int16: np.ndarray, render_int16: np.ndarray) -> np.ndarray:
        return self.apm.process_stream(capture_int16, render_int16)

    def reset(self) -> None:
        try:
            self.apm.reset()
        except Exception:  # noqa: BLE001
            pass


class _DfnPostProcessor:
    """DeepFilterNet3 neural noise + residual-echo + dereverb post-processor.

    Cascaded after the linear FDAF stage:  raw mic -> FDAF (kills linear echo)
    -> DFN (kills residual non-linear echo, background noise, room reverb).
    Closely mirrors WebRTC AEC3's architecture (linear AEC + neural NLP).
    """

    def __init__(self, frame_samples: int, sample_rate_hz: int) -> None:
        # Older deepfilternet (0.5.6) imports torchaudio.backend.common which
        # was removed in torchaudio 2.1+.  Stub it before df.enhance loads.
        import sys, types
        if "torchaudio.backend.common" not in sys.modules:
            ta_be = types.ModuleType("torchaudio.backend")
            class _AudioMetaData: pass
            m = types.ModuleType("torchaudio.backend.common")
            m.AudioMetaData = _AudioMetaData
            ta_be.common = m
            sys.modules["torchaudio.backend"]        = ta_be
            sys.modules["torchaudio.backend.common"] = m

        import torch
        from df.enhance import init_df, enhance
        self._torch = torch
        self._enhance = enhance
        self._model, self._state, _ = init_df()
        if self._state.sr() != sample_rate_hz:
            log.warning("DFN sample rate %d != engine %d; DFN expects 48 kHz",
                        self._state.sr(), sample_rate_hz)
        self._frame = frame_samples

    def process(self, pcm_int16: np.ndarray) -> np.ndarray:
        if pcm_int16.size == 0:
            return pcm_int16
        sig = pcm_int16.astype(np.float32) / 32768.0
        with self._torch.no_grad():
            out = self._enhance(self._model, self._state,
                                self._torch.from_numpy(sig).unsqueeze(0))
        cleaned = out.squeeze().cpu().numpy()
        return (cleaned * 32768.0).clip(-32768, 32767).astype(np.int16)


def make_post(frame_samples: int, sample_rate_hz: int, enable_dfn: bool):
    """Return an optional post-processor with .process(int16) -> int16, or None."""
    if not enable_dfn:
        return None
    try:
        post = _DfnPostProcessor(frame_samples, sample_rate_hz)
        log.info("AI post-processor: DeepFilterNet3 (CPU)")
        return post
    except Exception as e:  # noqa: BLE001
        log.warning("DeepFilterNet unavailable (%s); skipping AI post", e)
        return None


def make_aec(frame_samples: int, sample_rate_hz: int, ns: bool = True, vad: bool = True,
             prefer_webrtc: bool = True) -> Aec:
    if prefer_webrtc:
        try:
            ec = _WebrtcAec(frame_samples, sample_rate_hz, ns, vad)
            log.info("AEC: webrtc-audio-processing (AEC3)")
            return ec
        except Exception as e:  # noqa: BLE001
            log.info("AEC: webrtc binding unavailable (%s); falling back to FDAF", e)
    log.info("AEC: NumPy FDAF + Wiener post (block=%d, fs=%d Hz)", frame_samples, sample_rate_hz)
    return FdafAec(frame_samples)
