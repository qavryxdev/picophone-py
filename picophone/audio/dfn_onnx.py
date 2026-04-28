"""DeepFilterNet3 inference via onnxruntime + libdf (no PyTorch).

Replaces the torch-based DFN runtime with a 70 MB onnxruntime + 8 MB ONNX
model bundle. libdf (the Rust DSP crate that ships with the deepfilternet
pip package, ~5 MB) handles STFT analysis/synthesis and the ERB filterbank;
this module orchestrates the three ONNX models and the deep-filter step.

Pipeline (per audio chunk, sample rate 48 kHz):

    audio -- libdf.DF.analysis -->  spec [1, T, 481] complex64
    spec   -- libdf.erb         -->  erb_mag [1, T, 32]
    erb_mag -- libdf.erb_norm    -->  feat_erb [1, T, 32]
    spec[..., :96] -- libdf.unit_norm --> spec_feat [1, T, 96] complex64

    [feat_erb, feat_spec(re/im)] -> enc.onnx  -> e0..e3, emb, c0
    [emb, e3, e2, e1, e0]       -> erb_dec.onnx -> gain_mask [1, 1, T, 32]
    [emb, c0]                   -> df_dec.onnx  -> df_coefs [1, T, 96, 10]

    spec * libdf.erb_inv(gain_mask) -> spec_g  (mask in ERB domain expanded to 481 bins)
    deep_filter(spec_g, df_coefs)   -> spec_clean (5-frame complex sliding mul on first 96)
    libdf.DF.synthesis(spec_clean)   -> audio_out
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# DFN3 hyperparameters (must match the trained model — see assets/dfn3/config.ini).
SR          = 48000
FFT_SIZE    = 960
HOP_SIZE    = 480
NB_ERB      = 32
NB_DF       = 96
DF_ORDER    = 5
DF_LOOKAHEAD = 2
NORM_ALPHA   = 0.99   # df.utils.get_norm_alpha(False) for SR=48000


def _models_dir() -> Path:
    """Locate the bundled assets/dfn3 directory.

    Works when running from source (project root) and in Nuitka onefile
    (sys._MEIPASS-style, but Nuitka uses __file__ that points inside the
    extracted temp dir)."""
    here = Path(__file__).resolve().parent
    for cand in (
        here.parent.parent / "assets" / "dfn3",   # project root layout
        here.parent.parent / "dfn3",               # alternate
    ):
        if cand.is_dir():
            return cand
    raise FileNotFoundError("DFN3 ONNX models not found (assets/dfn3)")


def _to_complex64(arr: np.ndarray) -> np.ndarray:
    """Coerce libdf output (sometimes float32 with last-dim re/im) to complex64."""
    if np.iscomplexobj(arr):
        return arr.astype(np.complex64, copy=False)
    if arr.dtype == np.float32 and arr.shape[-1] == 2:
        return (arr[..., 0] + 1j * arr[..., 1]).astype(np.complex64)
    raise TypeError(f"unexpected libdf output dtype/shape {arr.dtype} {arr.shape}")


def _deep_filter(spec: np.ndarray, coefs: np.ndarray) -> np.ndarray:
    """5-frame complex sliding multiplication on the first NB_DF bins.

    spec:  [T, NB_F]    complex64     full spectrogram
    coefs: [T, NB_DF, DF_ORDER]  complex64
    Returns spec with first NB_DF bins replaced by the deep-filtered version.
    """
    T = spec.shape[0]
    spec_f = spec[:, :NB_DF]
    pad_front = DF_ORDER - 1 - DF_LOOKAHEAD   # = 2
    pad_back  = DF_LOOKAHEAD                  # = 2
    padded = np.pad(spec_f, ((pad_front, pad_back), (0, 0)))
    # sliding_window_view -> [T, NB_DF, DF_ORDER]
    unfolded = np.lib.stride_tricks.sliding_window_view(padded, DF_ORDER, axis=0)
    # element-wise multiply across the frame_size axis, sum over it
    filt = np.einsum("tfn,tfn->tf", unfolded, coefs).astype(np.complex64)
    spec_out = spec.copy()
    spec_out[:, :NB_DF] = filt
    return spec_out


class DfnOnnxPostProcessor:
    """Drop-in replacement for the torch-based _DfnPostProcessor.

    Same .process(int16_pcm) -> int16_pcm contract.

    Streaming strategy: DFN3's recurrent GRUs need a long context window
    to produce non-robotic output.  The exported ONNX has no state in/out,
    so each ort.run() call starts from h0=0.  We compensate by buffering
    audio into a sliding window of WINDOW_SAMPLES (default 200 ms = 10
    DFN hops) and processing the whole window every call.  Returns the
    chunk corresponding to the current input frame, which is now delayed
    by WINDOW_SAMPLES - frame_samples (~180 ms at 20 ms frames).
    """

    WINDOW_MS = 200            # 10 DFN hops; trade-off vs. mouth-to-ear latency

    def __init__(self, frame_samples: int, sample_rate_hz: int) -> None:
        if sample_rate_hz != SR:
            log.warning("DFN3 expects %d Hz, got %d — passthrough", SR, sample_rate_hz)
        self._frame = int(frame_samples)
        self._sr = int(sample_rate_hz)
        self._window = SR * self.WINDOW_MS // 1000     # 9600 samples at 48 kHz

        import libdf
        self._libdf = libdf
        self._df = libdf.DF(sr=SR, fft_size=FFT_SIZE, hop_size=HOP_SIZE,
                            nb_bands=NB_ERB, min_nb_erb_freqs=2)
        self._erb_widths = self._df.erb_widths()

        import onnxruntime as ort
        models = _models_dir()
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1     # one frame at a time, no win from threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        provider = ["CPUExecutionProvider"]
        self._enc     = ort.InferenceSession(str(models / "enc.onnx"),     opts, providers=provider)
        self._erb_dec = ort.InferenceSession(str(models / "erb_dec.onnx"), opts, providers=provider)
        self._df_dec  = ort.InferenceSession(str(models / "df_dec.onnx"),  opts, providers=provider)

        # Sliding-window buffers (int16 in, int16 out).  in_buf accumulates
        # raw mic; out_buf holds the last enhanced window so we can serve a
        # frame from its tail on each call.
        self._in_buf  = np.zeros(self._window, dtype=np.int16)
        self._out_buf = np.zeros(self._window, dtype=np.int16)
        log.info("DFN3-ONNX ready (window=%d ms, no torch). Models from %s",
                 self.WINDOW_MS, models)

    def _enhance_window(self, win_int16: np.ndarray) -> np.ndarray:
        """Run DFN3 on a fixed-size window (int16 -> int16, same length)."""
        audio = (win_int16.astype(np.float32) / 32768.0).reshape(1, -1)
        spec = self._df.analysis(audio)            # [1, T, 481] complex64

        erb_mag  = self._libdf.erb(spec, self._erb_widths)
        erb_feat = self._libdf.erb_norm(erb_mag, NORM_ALPHA)
        spec_feat = self._libdf.unit_norm(spec[..., :NB_DF].copy(), NORM_ALPHA)
        feat_erb_nn  = erb_feat[:, np.newaxis, :, :].astype(np.float32)
        re = np.real(spec_feat).astype(np.float32)
        im = np.imag(spec_feat).astype(np.float32)
        feat_spec_nn = np.stack([re[0], im[0]], axis=0)[np.newaxis, ...]

        e0, e1, e2, e3, emb, c0, _ = self._enc.run(
            None, {"feat_erb": feat_erb_nn, "feat_spec": feat_spec_nn})
        gain = self._erb_dec.run(
            None, {"emb": emb, "e3": e3, "e2": e2, "e1": e1, "e0": e0})[0]
        gain_full = self._libdf.erb_inv(gain[:, 0], self._erb_widths)
        spec_g = spec * gain_full.astype(np.complex64)

        df_out = self._df_dec.run(None, {"emb": emb, "c0": c0})
        coefs_arr = df_out[0]
        T = coefs_arr.shape[1]
        coefs = coefs_arr.reshape(1, T, NB_DF, DF_ORDER, 2)
        coefs_c = (coefs[..., 0] + 1j * coefs[..., 1]).astype(np.complex64)
        spec_clean = _deep_filter(spec_g[0], coefs_c[0])[np.newaxis]

        out = self._df.synthesis(spec_clean)
        out_pcm = (np.clip(out[0], -1.0, 1.0) * 32768.0).astype(np.int16)
        n = win_int16.size
        if out_pcm.size < n:
            out_pcm = np.pad(out_pcm, (0, n - out_pcm.size))
        elif out_pcm.size > n:
            out_pcm = out_pcm[:n]
        return out_pcm

    def process(self, pcm_int16: np.ndarray) -> np.ndarray:
        """Sliding-window streaming wrapper.

        On every captured frame we:
          1. shift _in_buf left by frame_samples and append the new frame at
             the tail (so the window holds the last WINDOW_MS of audio)
          2. enhance the entire window in one ort.run() call — DFN3's GRUs
             then have ~10 hops of context per pass, far better than the 2
             hops we'd give them with per-frame processing
          3. cache the enhanced window in _out_buf
          4. return the *tail* frame_samples of _out_buf, which corresponds
             to the freshly processed input — but with WINDOW_MS - frame
             samples of look-back already accounted for by the GRUs

        Latency: a sample appears in the output only after the window has
        rolled forward enough to include it, i.e. one frame after capture.
        Total added latency ≈ WINDOW_MS - frame_ms (~180 ms at default
        20 ms frame, 200 ms window) sitting in the GRU context.
        """
        n = pcm_int16.size
        if n == 0:
            return pcm_int16
        # advance buffer
        self._in_buf = np.concatenate([self._in_buf[n:], pcm_int16.astype(np.int16)])
        # process whole window
        self._out_buf = self._enhance_window(self._in_buf)
        # return the most recent frame from the cleaned window
        return self._out_buf[-n:].copy()
