"""FDAF echo canceller smoke test.

Synthesise a far-end (render) signal as a chirp + speech-like noise.
Convolve through a short room impulse response → that's the echo as recorded by mic.
Add a quiet near-end voice fragment to mic so we can check it survives.
Process frame-by-frame and measure ERLE (echo return loss enhancement) over time.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from picophone.audio.aec import FdafAec


def main() -> None:
    fs = 48000
    frame = 960
    rng = np.random.default_rng(42)

    duration_s = 6.0
    n = int(fs * duration_s)
    t = np.arange(n) / fs

    far_end = (0.5 * np.sin(2 * np.pi * 800 * t * (1 + 0.05 * np.sin(2 * np.pi * 0.3 * t)))
               + 0.2 * rng.standard_normal(n)).astype(np.float32)

    rir = np.zeros(int(fs * 0.05), dtype=np.float32)
    rir[12]  = 0.6
    rir[40]  = 0.4
    rir[120] = 0.25
    rir[400] = 0.1
    echo = np.convolve(far_end, rir, mode="full")[:n]

    near_end = np.zeros(n, dtype=np.float32)
    seg = slice(int(fs * 3.0), int(fs * 3.5))
    near_end[seg] = 0.15 * np.sin(2 * np.pi * 250 * t[seg])

    mic = (echo + near_end).astype(np.float32)
    mic_int  = np.clip(mic      * 32768.0, -32768, 32767).astype(np.int16)
    far_int  = np.clip(far_end  * 32768.0, -32768, 32767).astype(np.int16)

    aec = FdafAec(frame_samples=frame, mu=0.5)
    out = np.zeros(n, dtype=np.int16)
    n_frames = n // frame
    for i in range(n_frames):
        s = slice(i * frame, (i + 1) * frame)
        out[s] = aec.process(mic_int[s], far_int[s])

    def db_energy(x: np.ndarray) -> float:
        return 10.0 * np.log10(np.mean(x.astype(np.float32) ** 2) + 1e-9)

    # measure on the converged tail (last 2 s) of the far-end-only region [0, 3 s)
    converged = slice(int(fs * 2.0), int(fs * 3.0))
    erle = db_energy(mic_int[converged]) - db_energy(out[converged])

    near_in_mic_db = db_energy(mic_int[seg])
    near_in_out_db = db_energy(out[seg])
    near_kept_db   = near_in_out_db - db_energy(out[converged])

    print(f"ERLE on converged tail (2-3 s): {erle:.1f} dB  (target >= 15 dB)")
    print(f"Near-end voice (3-3.5 s):       in_mic={near_in_mic_db:.1f} dB, out={near_in_out_db:.1f} dB,"
          f" floor={db_energy(out[converged]):.1f} dB  (margin {near_kept_db:.1f} dB)")

    if erle < 15.0:
        print("FAIL: AEC did not converge enough")
        sys.exit(1)
    print("OK: FDAF AEC converged")


if __name__ == "__main__":
    main()
