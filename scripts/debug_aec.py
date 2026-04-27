"""Debug AEC: print ERLE every 100 blocks, max |W|, sigma_x."""
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

    n = int(fs * 6.0)
    t = np.arange(n) / fs
    far = (0.5 * np.sin(2 * np.pi * 800 * t * (1 + 0.05 * np.sin(2 * np.pi * 0.3 * t)))
           + 0.2 * rng.standard_normal(n)).astype(np.float32)
    rir = np.zeros(int(fs * 0.05), dtype=np.float32)
    rir[12]  = 0.6;  rir[40] = 0.4;  rir[120] = 0.25;  rir[400] = 0.1
    echo = np.convolve(far, rir, mode="full")[:n]
    mic = echo

    mic_int = np.clip(mic * 32768, -32768, 32767).astype(np.int16)
    far_int = np.clip(far * 32768, -32768, 32767).astype(np.int16)

    aec = FdafAec(frame_samples=frame, mu=0.05)

    n_frames = n // frame
    for i in range(n_frames):
        s = slice(i * frame, (i + 1) * frame)
        out = aec.process(mic_int[s], far_int[s])
        if i % 50 == 0:
            in_pow  = np.mean(mic_int[s].astype(np.float32) ** 2)
            out_pow = np.mean(out.astype(np.float32) ** 2)
            erle = 10 * np.log10((in_pow + 1) / (out_pow + 1))
            wmax = float(np.max(np.abs(aec.W)))
            S_avg = float(np.mean(aec._S))
            print(f"block {i:3d}  ERLE={erle:+6.2f} dB  S_avg={S_avg:.4f}  "
                  f"|W|max={wmax:.3e}  |out|max={int(np.max(np.abs(out)))}")


if __name__ == "__main__":
    main()
