#!/usr/bin/env python3
"""V2 translation layer: X-arm transcripts -> zh-TW text, two prompts.

  P0 — kikemu's original interpreter systemInstruction, unchanged
       (handoff-v2 §6.3: deliberately not term-aware).
  P1 — P0 + explicit rule: English terms from the vocab stay in English.
       P1 receives the SAME vocab the listening arm used (domain or slides);
       for the no-vocab arm X, P1 uses the domain vocab (a product would
       always have at least the domain pack at the translation layer).

Writes exp2/results/raw/<arm>_translate_<P0|P1>/<stem>.json with usage.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

V1 = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(V1))
from prompts import INTERPRETER_SYSTEM  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VOCAB = ROOT / "corpus" / "vocab"
G_KEY = os.environ["gemini_key"]
MODEL = "gemini-3.5-flash"

P1_SUFFIX = (
    "\n\n重要:以下清單中的英文術語在譯文中一律維持英文原文,不要翻成中文:\n{terms}"
)

USER_TEMPLATE = (
    "以下是課程演講的語音辨識書き起こし(中文夾雜英文術語)。"
    "請整理成通順的台灣繁體中文文字。\n\n{transcript}"
)


def vocab_terms(arm: str, seg: str) -> list[str]:
    kind = "slides" if "sli" in arm else "domain"
    f = VOCAB / (f"slides_{seg}.json" if kind == "slides" else "domain.json")
    return [e["content"] for e in json.loads(f.read_text())]


def translate(transcript: str, system: str) -> dict:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"parts": [{"text": USER_TEMPLATE.format(transcript=transcript)}]}],
            "generationConfig": {"temperature": 0.2},
        },
        timeout=300,
    )
    r.raise_for_status()
    return r.json()


def main():
    arms = sys.argv[1:] or ["X_cmn", "X_multi", "Xdom_multi", "Xsli_multi"]
    for arm in arms:
        indir = ROOT / "results" / "raw" / arm
        for prompt_tag in ("P0", "P1"):
            outdir = ROOT / "results" / "raw" / f"{arm}_translate_{prompt_tag}"
            outdir.mkdir(parents=True, exist_ok=True)
            for f in sorted(indir.glob("*.json")):
                out = outdir / f.name
                if out.exists():
                    continue
                d = json.loads(f.read_text())
                seg = f.stem.split("__")[0]
                system = INTERPRETER_SYSTEM
                if prompt_tag == "P1":
                    terms = vocab_terms(arm, seg)
                    system = system + P1_SUFFIX.format(terms=", ".join(terms[:300]))
                resp = translate(d["transcript"], system)
                text = resp["candidates"][0]["content"]["parts"][0]["text"]
                out.write_text(json.dumps({
                    "arm": arm, "prompt": prompt_tag, "file": d["file"],
                    "translation": text, "usage": resp.get("usageMetadata"),
                }, ensure_ascii=False), encoding="utf-8")
                print(f"translated {arm} {prompt_tag} {f.name}", flush=True)
                time.sleep(0.5)


if __name__ == "__main__":
    main()
