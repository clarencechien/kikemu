#!/usr/bin/env python3
"""Reference-building step 1: three independent transcriptions per segment.

  - Speechmatics batch, language=cmn (enhanced)
  - gemini-3.5-flash   (verbatim code-mixed transcription prompt)
  - gemini-3.6-flash   (same prompt, different model generation)

No official transcript exists for these lectures, so the reference is an
adjudicated merge (step 2) with every disagreement logged for human review.
Outputs exp2/corpus/verify/{seg}.{engine}.txt
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "corpus" / "wav"
OUT = ROOT / "corpus" / "verify"
S_KEY = os.environ["s_key"]
G_KEY = os.environ["gemini_key"]

PROMPT = (
    "請將這段台灣的機器學習課程錄音逐字轉寫。\n"
    "- 講者以中文授課,夾雜大量英文術語(例如 network、function、attention)。\n"
    "- 中文寫繁體中文,英文術語保留英文原文拼寫,不要翻成中文。\n"
    "- 逐字轉寫,保留口語(這個、那、對不對),不要改寫、不要摘要。\n"
    "- 只輸出轉寫文字,不要任何說明。"
)


def sm_batch(wav: Path) -> str:
    conf = {"type": "transcription",
            "transcription_config": {"language": "cmn", "operating_point": "enhanced"}}
    r = requests.post("https://asr.api.speechmatics.com/v2/jobs",
                      headers={"Authorization": f"Bearer {S_KEY}"},
                      files={"data_file": (wav.name, wav.open("rb"), "audio/wav"),
                             "config": (None, json.dumps(conf))}, timeout=180)
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(180):
        time.sleep(5)
        st = requests.get(f"https://asr.api.speechmatics.com/v2/jobs/{job}",
                          headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60).json()["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "deleted"):
            raise RuntimeError(st)
    r = requests.get(f"https://asr.api.speechmatics.com/v2/jobs/{job}/transcript?format=txt",
                     headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60)
    r.raise_for_status()
    return r.content.decode("utf-8")


def gemini_tx(wav: Path, model: str) -> str:
    b64 = base64.b64encode(wav.read_bytes()).decode()
    body = {"contents": [{"parts": [{"text": PROMPT},
                                    {"inline_data": {"mime_type": "audio/wav", "data": b64}}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 16384}}
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json=body, timeout=600)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for seg in ["S1", "S2", "S3"]:
        wav = WAV / f"{seg}.wav"
        for name, fn in [("sm", lambda w: sm_batch(w)),
                         ("gm35", lambda w: gemini_tx(w, "gemini-3.5-flash")),
                         ("gm36", lambda w: gemini_tx(w, "gemini-3.6-flash"))]:
            f = OUT / f"{seg}.{name}.txt"
            if f.exists():
                continue
            print(f"{seg}.{name} ...", file=sys.stderr)
            f.write_text(fn(wav), encoding="utf-8")
            print(f"{seg}.{name}: {len(f.read_text())} ch", file=sys.stderr)


if __name__ == "__main__":
    main()
