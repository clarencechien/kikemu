#!/usr/bin/env python3
"""Exp3 fast (causal, streamable) preprocessors — handoff-v3 §3.

  webrtc   WebRTC APM noise suppression (webrtc-noise-gain, 10 ms frames,
           NS level 3, AGC off). The capture-side processing our raw-file
           injection bypassed; every browser call runs this.
  sg       Spectral gating (noisereduce), causal streaming simulation:
           1 s hops processed with only past context (2 s) + current block.
  blend    Artifact-aware mixing (Iwamoto et al. 2022):
           0.75 * enhanced(webrtc) + 0.25 * raw.

RNNoise: pyrnnoise is incompatible with installed PyAV (av.option removed);
documented as a coverage gap.

Outputs corpus/conditions_pp/{seg}__{cond}__{pp}.wav and prints RTF
(processing_time / audio_time; must be < 0.3 to qualify as "fast").
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
OUT = ROOT / "corpus" / "conditions_pp"
SR = 16000
CONDS = ["N0", "N1", "N2", "N3"]


def pp_webrtc(x16: np.ndarray) -> np.ndarray:
    from webrtc_noise_gain import AudioProcessor
    proc = AudioProcessor(0, 3)  # AGC off, NS level 3 (max)
    frame = 160  # 10 ms @ 16 kHz
    pcm = x16.tobytes()
    out = bytearray()
    for i in range(0, len(pcm) - frame * 2 + 1, frame * 2):
        r = proc.Process10ms(pcm[i : i + frame * 2])
        out.extend(r.audio)
    return np.frombuffer(bytes(out), dtype=np.int16)


def pp_sg(x16: np.ndarray) -> np.ndarray:
    import noisereduce as nr
    x = x16.astype(np.float32) / 32768.0
    hop = SR  # 1 s blocks
    ctx = 2 * SR  # 2 s of past context
    out = np.zeros_like(x)
    for start in range(0, len(x), hop):
        end = min(start + hop, len(x))
        a = max(0, start - ctx)
        seg = x[a:end]
        den = nr.reduce_noise(y=seg, sr=SR, stationary=False, prop_decrease=0.9)
        out[start:end] = den[start - a : end - a]
    return np.clip(out * 32768.0, -32768, 32767).astype(np.int16)


def pp_blend(x16: np.ndarray) -> np.ndarray:
    enh = pp_webrtc(x16).astype(np.float32)
    raw = x16[: len(enh)].astype(np.float32)
    return np.clip(0.75 * enh + 0.25 * raw, -32768, 32767).astype(np.int16)


PPS = {"webrtc": pp_webrtc, "sg": pp_sg, "blend": pp_blend}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    only_pp = sys.argv[1:] or list(PPS)
    rtf_log = {}
    for wav in sorted(COND.glob("*.wav")):
        seg, cond = wav.stem.split("__")
        if cond not in CONDS:
            continue
        x, sr = sf.read(wav, dtype="int16")
        assert sr == SR
        for pp in only_pp:
            dst = OUT / f"{seg}__{cond}__{pp}.wav"
            if dst.exists():
                continue
            t0 = time.perf_counter()
            y = PPS[pp](x)
            dt = time.perf_counter() - t0
            rtf = dt / (len(x) / SR)
            rtf_log.setdefault(pp, []).append(rtf)
            # peak-normalize to match degrade.py convention
            yf = y.astype(np.float32)
            yf = yf / np.max(np.abs(yf)) * (0.89 * 32767)
            sf.write(dst, yf.astype(np.int16), SR, subtype="PCM_16")
            print(f"{dst.name}  rtf={rtf:.3f}", flush=True)
    summary = {pp: {"rtf_mean": round(float(np.mean(v)), 3), "rtf_max": round(float(np.max(v)), 3)}
               for pp, v in rtf_log.items()}
    if summary:
        (OUT / "rtf.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary))


if __name__ == "__main__":
    main()
