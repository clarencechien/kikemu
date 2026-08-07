#!/usr/bin/env python3
"""Exp4: synthesize directional-pickup conditions (handoff-v4 §2).

y = x ⊛ h_de + β·(x ⊛ h_late) + γ·noise
  h_de   = RIR direct + early reflections (first arrival .. +50 ms)
  h_late = late tail
  β = 10^(−ΔDRR/20); noise re-scaled to baseline SNR + ΔSNR using the SAME
  noise slice (same seed) as the original degraded file, so comparisons
  against N2/N3 (and M2/M3) are exactly paired.

Profiles (frozen): card(+5,+3)  conf(+9,+6)  lav(+18,+15).
Validation mode (--check): rebuild with β=1, γ=1 and report token-level
difference vs the original condition files — must be ~0.

Usage: directivity.py [--exp 1|2] [--check]
"""
import json
import sys
import zipfile
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, str(Path(__file__).resolve().parent))
from degrade import load_wav_from_zip, speech_rms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SR = 16000
SEED = 20260807
PEAK = 0.89
EARLY_S = 0.05

PROFILES = {"card": (5.0, 3.0), "conf": (9.0, 6.0), "lav": (18.0, 15.0)}

EXP = {
    "1": {
        "wav": ROOT / "corpus" / "wav",
        "out": ROOT / "corpus" / "conditions_dir",
        "orig": ROOT / "corpus" / "conditions",
        "rir_zip": "mit_ir.zip",
        "rir_name": "Audio/h213_SubwayStation_CentralSquareCambridge_1txts.wav",
        "noises": {"N2": ("PCAFETER_16k.zip", "PCAFETER/ch01.wav", 15.0),
                   "N3": ("PCAFETER_16k.zip", "PCAFETER/ch01.wav", 8.0)},
        "reverb_only": "N1",
        "segs_glob": "*.wav",
        "wav_of": lambda seg: seg + ".wav",
    },
    "2": {
        "wav": ROOT / "exp2" / "corpus" / "wav",
        "out": ROOT / "exp2" / "corpus" / "conditions_dir",
        "orig": ROOT / "exp2" / "corpus" / "conditions",
        "rir_zip": "mit_ir.zip",
        "rir_name": "Audio/h100_Classroom_2txts.wav",
        "noises": {"M2": ("OOFFICE_16k.zip", "OOFFICE/ch01.wav", 20.0),
                   "M3": ("OMEETING_16k.zip", "OMEETING/ch01.wav", 12.0)},
        "reverb_only": "M1",
        "segs_glob": "*.wav",
        "wav_of": lambda seg: seg + ".wav",
    },
}

NSRC = ROOT / "corpus" / "noise_src"


def norm(x):
    return x / np.max(np.abs(x)) * PEAK


def load_rir(cfg):
    ir, sr = load_wav_from_zip(zipfile.ZipFile(NSRC / cfg["rir_zip"]), cfg["rir_name"])
    if sr != SR:
        ir = signal.resample_poly(ir, SR, sr)
    ir = ir / np.max(np.abs(ir))
    i0 = int(np.argmax(np.abs(ir)))
    cut = i0 + int(EARLY_S * SR)
    de = ir[:cut]
    late = np.zeros_like(ir)
    late[cut:] = ir[cut:]
    return de, late


def synth(x, de, late, noise, snr_db, rng, beta, dsnr):
    xde = signal.fftconvolve(x, de)[: len(x)]
    xlate = signal.fftconvolve(x, late)[: len(x)]
    # k: the normalization constant degrade.py applied to the BASELINE mix.
    # Reusing it keeps the direct-path level identical across profiles.
    base = xde + xlate
    k = PEAK / np.max(np.abs(base))
    y = k * (xde + beta * xlate)
    if noise is not None:
        off = rng.integers(0, len(noise) - len(x))
        nz = noise[off : off + len(x)]
        s_rms = speech_rms(k * base)  # == speech_rms of degrade.py's reverbed
        scale = s_rms / (np.sqrt((nz**2).mean()) * 10 ** ((snr_db + dsnr) / 20))
        y = y + nz * scale
    return norm(y)


def main():
    exp = "1"
    check = "--check" in sys.argv
    if "--exp" in sys.argv:
        exp = sys.argv[sys.argv.index("--exp") + 1]
    cfg = EXP[exp]
    cfg["out"].mkdir(parents=True, exist_ok=True)
    de, late = load_rir(cfg)
    noise_cache = {}

    def get_noise(zname, member):
        if (zname, member) not in noise_cache:
            n, sr = load_wav_from_zip(zipfile.ZipFile(NSRC / zname), member)
            assert sr == SR
            noise_cache[(zname, member)] = n
        return noise_cache[(zname, member)]

    for wav in sorted(cfg["wav"].glob(cfg["segs_glob"])):
        seg = wav.stem
        x, sr = sf.read(wav, dtype="float64")
        assert sr == SR
        x = norm(x)
        if check:
            # rebuild N-equivalents with beta=1, dsnr=0; compare to originals
            for cond, (zn, mem, snr) in cfg["noises"].items():
                rng = np.random.default_rng(SEED + zlib.crc32(f"{seg}:{cond}".encode()))
                y = synth(x, de, late, get_noise(zn, mem), snr, rng, 1.0, 0.0)
                orig, _ = sf.read(cfg["orig"] / f"{seg}__{cond}.wav", dtype="float64")
                n = min(len(y), len(orig))
                err = np.sqrt(np.mean((y[:n] - orig[:n]) ** 2)) / np.sqrt(np.mean(orig[:n] ** 2))
                print(f"CHECK {seg}__{cond}: rel_rms_err={err:.4f}")
            continue
        for cond, (zn, mem, snr) in cfg["noises"].items():
            for prof, (ddrr, dsnr) in PROFILES.items():
                dst = cfg["out"] / f"{seg}__{cond}__{prof}.wav"
                if dst.exists():
                    continue
                rng = np.random.default_rng(SEED + zlib.crc32(f"{seg}:{cond}".encode()))
                beta = 10 ** (-ddrr / 20)
                y = synth(x, de, late, get_noise(zn, mem), snr, rng, beta, dsnr)
                sf.write(dst, y.astype(np.float32), SR, subtype="PCM_16")
                print(f"{dst.name}")
        # reverb-only condition with directivity (for the lav→N0 question)
        for prof, (ddrr, _) in PROFILES.items():
            dst = cfg["out"] / f"{seg}__{cfg['reverb_only']}__{prof}.wav"
            if dst.exists():
                continue
            beta = 10 ** (-ddrr / 20)
            y = synth(x, de, late, None, 0.0, np.random.default_rng(0), beta, 0.0)
            sf.write(dst, y.astype(np.float32), SR, subtype="PCM_16")
            print(f"{dst.name}")


if __name__ == "__main__":
    main()
