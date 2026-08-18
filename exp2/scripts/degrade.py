#!/usr/bin/env python3
"""V2 acoustic conditions M0-M3 (lecture/meeting scenario, handoff-v2 §5).

  M0  original slice (peak-normalized)
  M1  + lecture-hall RIR (MIT IR Survey, T60 closest to 0.8 s)
  M2  M1 + office/HVAC noise  (DEMAND OOFFICE ch01) @ SNR 20 dB
  M3  M1 + meeting-chatter    (DEMAND OMEETING ch01) @ SNR 12 dB

Same machinery as v1 scripts/degrade.py: speech-active RMS for SNR,
fixed seeds, peak norm after every step. 3 segs x 4 conds = 12 files.
"""
import json
import sys
import zipfile
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

V1 = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(V1))
from degrade import load_wav_from_zip, schroeder_t60, speech_rms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NSRC = ROOT.parent / "corpus" / "noise_src"
WAV = ROOT / "corpus" / "wav"
OUT = ROOT / "corpus" / "conditions"
SR = 16000
SEED = 20260807
TARGET_RT60 = 0.8
PEAK = 0.89


def norm(x):
    return x / np.max(np.abs(x)) * PEAK


def pick_rir():
    """Deterministic rule: among IRs recorded in a Classroom (matching the
    lecture-hall scenario), pick T60 closest to 0.8 s. (The unconstrained
    closest-T60 IR is a bathroom — right decay, wrong space.)"""
    zf = zipfile.ZipFile(NSRC / "mit_ir.zip")
    best = None
    for name in sorted(zf.namelist()):
        if not name.endswith(".wav") or "__MACOSX" in name or "Classroom" not in name:
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
    return ir / np.max(np.abs(ir)), name, t60


def add_noise(x, noise, snr_db, rng):
    off = rng.integers(0, len(noise) - len(x))
    nz = noise[off: off + len(x)]
    scale = speech_rms(x) / (np.sqrt((nz ** 2).mean()) * 10 ** (snr_db / 20))
    return x + nz * scale


def build(wav_dir=None, out_dir=None, segs=("S1", "S2", "S3")):
    """產生 M0–M3。exp5(領域外對照)呼叫同一支,確保 RIR / 噪音 / 種子完全一致——
       另外複製一份實作就等於換了聲學條件,跟 exp2 的數字沒得比。
       種子是 SEED + crc32(f"{seg}:{tag}"),所以換 segs 不會動到既有段落的輸出。"""
    wav_dir = Path(wav_dir) if wav_dir else WAV
    out_dir = Path(out_dir) if out_dir else OUT
    OUT_ = out_dir
    OUT_.mkdir(parents=True, exist_ok=True)
    rir, rir_name, t60 = pick_rir()
    print(f"RIR: {rir_name} T60={t60:.3f}s")
    def try_noise(zip_name, member):
        p = NSRC / zip_name
        try:
            n, sr = load_wav_from_zip(zipfile.ZipFile(p), member)
            assert sr == SR
            return n
        except Exception as e:
            print(f"noise {zip_name} unavailable ({e}); skipping its condition for now")
            return None

    office = try_noise("OOFFICE_16k.zip", "OOFFICE/ch01.wav")
    meeting = try_noise("OMEETING_16k.zip", "OMEETING/ch01.wav")
    meta = {"rir": rir_name, "rir_t60": round(t60, 3), "seed": SEED,
            "M2": "OOFFICE ch01 @20dB", "M3": "OMEETING ch01 @12dB"}
    for seg in segs:
        # Skip segments whose source slice is absent, same as the missing-noise
        # branch above. Phase A of the X-breeze arm only needs S1 and S3, and the
        # per-segment seed (SEED + crc32(f"{seg}:{tag}")) means skipping one
        # segment cannot change any other segment's output.
        if not (wav_dir / f"{seg}.wav").exists():
            print(f"{seg}.wav not present; skipping")
            continue
        x, sr = sf.read(wav_dir / f"{seg}.wav", dtype="float64")
        assert sr == SR
        x = norm(x)
        rev = norm(signal.fftconvolve(x, rir)[: len(x)])
        conds = {"M0": x, "M1": rev}
        for tag, noise, snr in (("M2", office, 20.0), ("M3", meeting, 12.0)):
            if noise is None:
                continue
            rng = np.random.default_rng(SEED + zlib.crc32(f"{seg}:{tag}".encode()))
            conds[tag] = norm(add_noise(rev, noise, snr, rng))
        for tag, y in conds.items():
            sf.write(OUT_ / f"{seg}__{tag}.wav", y.astype(np.float32), SR, subtype="PCM_16")
        print(seg, "->", list(conds))
    (OUT_ / "degrade_meta.json").write_text(json.dumps(meta, indent=1))


def main():
    build()


if __name__ == "__main__":
    main()
