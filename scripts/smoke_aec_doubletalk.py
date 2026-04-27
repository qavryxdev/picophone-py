"""Double-talk test: near-end voice plays simultaneously with far-end echo.
The Wiener post-filter must NOT crush near-end speech below the residual echo
floor. We measure how much of the near-end energy survives vs. how much echo
is suppressed in the same window.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from picophone.audio.aec import FdafAec


def db_energy(x: np.ndarray) -> float:
    return 10.0 * np.log10(np.mean(x.astype(np.float32) ** 2) + 1e-9)


def main() -> None:
    fs = 48000
    frame = 960
    rng = np.random.default_rng(42)

    n = int(fs * 6.0)
    t = np.arange(n) / fs

    far = (0.5 * np.sin(2 * np.pi * 800 * t * (1 + 0.05 * np.sin(2 * np.pi * 0.3 * t)))
           + 0.2 * rng.standard_normal(n)).astype(np.float32)

    rir = np.zeros(int(fs * 0.05), dtype=np.float32)
    rir[12]  = 0.6;  rir[40] = 0.4;  rir[120] = 0.25;  rir[400] = 0.1
    echo = np.convolve(far, rir, mode="full")[:n]

    # Near-end VOICE: 250 Hz tone overlapping the converged tail (seconds 4-5)
    # so the canceller has had time to adapt before double-talk starts.
    near = np.zeros(n, dtype=np.float32)
    seg_dt   = slice(int(fs * 4.0), int(fs * 5.0))     # double-talk window
    seg_only = slice(int(fs * 5.5), int(fs * 6.0))     # near-end-only at end
    near[seg_dt]   = 0.20 * np.sin(2 * np.pi * 250 * t[seg_dt])
    near[seg_only] = 0.20 * np.sin(2 * np.pi * 250 * t[seg_only])

    mic = (echo + near).astype(np.float32)
    mic_int = np.clip(mic     * 32768, -32768, 32767).astype(np.int16)
    far_int = np.clip(far     * 32768, -32768, 32767).astype(np.int16)
    near_int = np.clip(near   * 32768, -32768, 32767).astype(np.int16)

    aec = FdafAec(frame_samples=frame)
    out = np.zeros(n, dtype=np.int16)
    for i in range(n // frame):
        s = slice(i * frame, (i + 1) * frame)
        out[s] = aec.process(mic_int[s], far_int[s])

    # 1. Echo suppression on the converged-tail far-end-only window 2-3 s
    erle = db_energy(mic_int[int(fs*2):int(fs*3)]) - db_energy(out[int(fs*2):int(fs*3)])

    # 2. Near-end voice survival: compare near_int[seg_dt] (clean reference)
    #    with out[seg_dt].  Correlation tells us how much voice came through.
    nref = near_int[seg_dt].astype(np.float32)
    o    = out[seg_dt].astype(np.float32)
    rho  = float(np.dot(nref, o) / (np.linalg.norm(nref) * np.linalg.norm(o) + 1e-9))

    # 3. Near-end-only window: voice should pass nearly unchanged (gain back to 1)
    near_only_in  = db_energy(near_int[seg_only])
    near_only_out = db_energy(out[seg_only])

    print(f"ERLE on far-end-only (2-3 s):    {erle:.1f} dB")
    print(f"Double-talk (4-5 s):             near-end correlation = {rho:.3f}  "
          f"(0=lost, 1=perfect)")
    print(f"Near-end-only (5.5-6 s):         input {near_only_in:.1f} dB -> "
          f"output {near_only_out:.1f} dB  (loss {near_only_in - near_only_out:.1f} dB)")

    if erle < 15:
        print("FAIL: echo not suppressed enough"); sys.exit(1)
    if rho < 0.3:
        print("FAIL: near-end voice crushed during double-talk"); sys.exit(1)
    if (near_only_in - near_only_out) > 6:
        print("FAIL: near-end-only voice attenuated more than 6 dB"); sys.exit(1)
    print("OK: AEC suppresses echo while preserving the near-end speaker")


if __name__ == "__main__":
    main()
