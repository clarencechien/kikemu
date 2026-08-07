#!/usr/bin/env python3
"""Synthesize the 5 acoustic conditions (N0-N4) for each corpus segment.

Materials (real, not synthetic):
  - RIR: MIT IR Survey (McDermott lab), the IR whose Schroeder T60 is
    closest to 0.6 s among indoor spaces. Selection is deterministic.
  - Crowd noise: DEMAND PCAFETER (cafeteria babble), ch01, 16 kHz.

Conditions:
  N0  original (peak-normalized only)
  N1  RIR convolution (RT60 ~ 0.6 s indoor)
  N2  N1 + cafeteria babble @ SNR 15 dB (speech-active RMS)
  N3  N1 + cafeteria babble @ SNR  8 dB
  N4  bandpass 300-3400 Hz + mild clipping + RIR   (PA loudspeaker)

Determinism: RNG = numpy default_rng seeded with SEED + segment/condition
tag; noise offsets and nothing else are random. Peak normalization after
every chain step that can clip. Output: 16 kHz mono PCM16,
corpus/conditions/{seg}__{cond}.wav
"""
import io
import json
import zipfile
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "corpus" / "wav"
NSRC = ROOT / "corpus" / "noise_src"
OUT = ROOT / "corpus" / "conditions"
SR = 16000
SEED = 20260807
TARGET_RT60 = 0.6
PEAK = 0.89


def load_wav_from_zip(zf: zipfile.ZipFile, name: str) -> tuple[np.ndarray, int]:
    data, sr = sf.read(io.BytesIO(zf.read(name)), dtype="float64", always_2d=True)
    return data[:, 0], sr


def schroeder_t60(ir: np.ndarray, sr: int) -> float:
    """T60 via T20 extrapolation on the Schroeder decay curve."""
    e = ir.astype(np.float64) ** 2
    sch = np.cumsum(e[::-1])[::-1]
    sch = 10 * np.log10(np.maximum(sch / sch[0], 1e-12))
    def t_at(db):
        idx = np.argmax(sch <= db)
        return idx / sr if sch[idx] <= db else None
    t5, t25 = t_at(-5), t_at(-25)
    if t5 is None or t25 is None:
        return float("nan")
    return 3.0 * (t25 - t5)


def pick_rir() -> tuple[np.ndarray, str, float]:
    zf = zipfile.ZipFile(NSRC / "mit_ir.zip")
    best = None
    for name in sorted(zf.namelist()):
        if not name.endswith(".wav") or "__MACOSX" in name:
            continue
        ir, sr = load_wav_from_zip(zf, name)
        if sr != SR:
            ir = signal.resample_poly(ir, SR, sr)
        t60 = schroeder_t60(ir, SR)
        if np.isnan(t60):
            continue
        d = abs(t60 - TARGET_RT60)
        if best is None or d < best[0]:
            best = (d, name, ir, t60)
    _, name, ir, t60 = best
    ir = ir / np.max(np.abs(ir))
    return ir, name, t60


def speech_rms(x: np.ndarray) -> float:
    """RMS over speech-active 50 ms frames (silence/music-only tails kept out
    by an energy gate at 10% of the loudest frame's RMS)."""
    fl = int(0.05 * SR)
    n = len(x) // fl
    frames = x[: n * fl].reshape(n, fl)
    rms = np.sqrt((frames**2).mean(axis=1))
    gate = rms.max() * 0.1
    act = rms[rms > gate]
    return float(np.sqrt((act**2).mean()))


def norm(x: np.ndarray) -> np.ndarray:
    return x / np.max(np.abs(x)) * PEAK


def add_noise(x: np.ndarray, noise: np.ndarray, snr_db: float, rng) -> np.ndarray:
    off = rng.integers(0, len(noise) - len(x))
    nz = noise[off : off + len(x)]
    s_rms, n_rms = speech_rms(x), np.sqrt((nz**2).mean())
    scale = s_rms / (n_rms * 10 ** (snr_db / 20))
    return x + nz * scale


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    rir, rir_name, rt60 = pick_rir()
    print(f"RIR: {rir_name}  T60={rt60:.3f}s")

    zf = zipfile.ZipFile(NSRC / "PCAFETER_16k.zip")
    noise, nsr = load_wav_from_zip(zf, "PCAFETER/ch01.wav")
    assert nsr == SR, nsr

    bp = signal.butter(4, [300, 3400], btype="bandpass", fs=SR, output="sos")
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    meta = {"rir": rir_name, "rir_t60": round(rt60, 3), "noise": "DEMAND PCAFETER ch01", "seed": SEED, "conditions": {}}

    for seg_id, spec in picks.items():
        x, sr = sf.read(WAV / spec["wav"], dtype="float64")
        assert sr == SR
        x = norm(x)
        reverbed = norm(signal.fftconvolve(x, rir)[: len(x)])
        conds = {"N0": x, "N1": reverbed}
        for tag, snr in (("N2", 15.0), ("N3", 8.0)):
            rng = np.random.default_rng(SEED + zlib.crc32(f"{seg_id}:{tag}".encode()))
            conds[tag] = norm(add_noise(reverbed, noise, snr, rng))
        pa = signal.sosfilt(bp, x)
        pa = norm(pa)
        pa = np.clip(pa, -0.5 * np.max(np.abs(pa)), 0.5 * np.max(np.abs(pa)))  # mild clipping
        conds["N4"] = norm(signal.fftconvolve(pa, rir)[: len(x)])
        for tag, y in conds.items():
            sf.write(OUT / f"{seg_id}__{tag}.wav", y.astype(np.float32), SR, subtype="PCM_16")
        rec = {t: round(float(np.sqrt((y**2).mean())), 4) for t, y in conds.items()}
        meta["conditions"][seg_id] = rec
        print(seg_id, "->", list(conds))

    (OUT / "degrade_meta.json").write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main()
