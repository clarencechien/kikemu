#!/usr/bin/env python3
"""Second hop for arms C / C+: translate Speechmatics transcripts to zh-TW
with Gemini, using the SAME interpreter system prompt as arm A (handoff §4).

Writes results/raw/<arm>_translate/<seg>__<cond>.json with usage metadata.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompts import INTERPRETER_SYSTEM, TRANSLATE_USER_TEMPLATE

ROOT = Path(__file__).resolve().parent.parent
G_KEY = os.environ["gemini_key"]
MODEL = "gemini-3.5-flash"


def translate(transcript: str) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "contents": [
            {"parts": [{"text": TRANSLATE_USER_TEMPLATE.format(transcript=transcript)}]}
        ],
        "generationConfig": {"temperature": 0.2},
    }
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=300,
    )
    r.raise_for_status()
    return r.json()


def main():
    for arm in sys.argv[1:] or ["C", "Cplus"]:
        indir = ROOT / "results" / "raw" / arm
        outdir = ROOT / "results" / "raw" / f"{arm}_translate"
        outdir.mkdir(parents=True, exist_ok=True)
        for f in sorted(indir.glob("*.json")):
            out = outdir / f.name
            if out.exists():
                continue
            d = json.loads(f.read_text())
            resp = translate(d["transcript"])
            text = resp["candidates"][0]["content"]["parts"][0]["text"]
            out.write_text(
                json.dumps(
                    {
                        "arm": arm,
                        "file": d["file"],
                        "model": MODEL,
                        "translation": text,
                        "usage": resp.get("usageMetadata"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            print(f"translated {arm} {f.name}", flush=True)
            time.sleep(1)


if __name__ == "__main__":
    main()
