#!/usr/bin/env python3
"""Slice the three lecture audios into 5-minute segments, 16 kHz mono.

Sources (documented, audio itself is NOT committed - see .gitignore):
  S1: ML2021 自注意力機制(Self-attention)(上), duration 1698s — identical
      duration to the official YouTube upload (hYdO9CscNes), i.e. the same
      recording, obtained via a public mirror because YouTube media downloads
      are blocked from this environment (bot check).
  S2: ML2021 生成式对抗网络(GAN)(一), 2348s, same course mirror.
  S3: 生成式AI導論 2024 第1講:生成式AI是什麼, 1769s.

Slice offsets are fixed constants chosen to avoid the course-admin intro and
land in continuous body exposition (handoff-v2 §2 selection rules).
"""
import subprocess
from pathlib import Path

import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
ROOT = Path(__file__).resolve().parent.parent
SP = Path("/tmp/claude-0/-home-user-kikemu/5605051a-5678-5cbe-839f-18cc17c6d816/scratchpad")
WAV = ROOT / "corpus" / "wav"

SLICES = {
    "S1": ("S1_selfattn.m4a", 360, 660),
    "S2": ("S2_gan.m4a", 420, 720),
    # S3 window moved from 300-600 to 900-1200 after a term-density probe
    # (8 -> 13 latin runs; ChatGPT/DALL-E/Stable Diffusion/Transformer),
    # decided BEFORE any arm ran on S3.
    "S3": ("S3_genai.m4a", 900, 1200),
}


def main():
    WAV.mkdir(parents=True, exist_ok=True)
    for seg, (src, a, b) in SLICES.items():
        out = WAV / f"{seg}.wav"
        if out.exists():
            continue
        subprocess.run(
            [FF, "-y", "-i", str(SP / src), "-ss", str(a), "-to", str(b),
             "-ac", "1", "-ar", "16000", "-sample_fmt", "s16", str(out)],
            check=True, capture_output=True,
        )
        print(seg, "->", out.name, f"{b-a}s")


if __name__ == "__main__":
    main()
