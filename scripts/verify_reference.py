#!/usr/bin/env python3
"""Verify page text against audio using two independent ASR passes.

Protocol (documented in report):
  1. Speechmatics batch (enhanced, ja) transcribes each candidate wav.
  2. Gemini (gemini-3.5-flash) transcribes the same wav (inline audio).
  3. Page text is normalized (reading glosses in parentheses removed,
     punctuation stripped) and compared to both ASR outputs by CER.
  4. A segment's reference is ADOPTED from page text only if both engines
     independently show low CER against it. Discrepancies are inspected
     and the reference corrected to the majority (2-of-3) reading.
For excerpt-style audio (sakai series), the spoken subrange of the page
text is located by alignment and the reference is trimmed to it.
"""
import base64
import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "corpus" / "wav"
OUT = ROOT / "corpus" / "verify"

S_KEY = os.environ["s_key"]
G_KEY = os.environ["gemini_key"]
GEMINI_TEXT_MODEL = "gemini-3.5-flash"

GLOSS_RE = re.compile(r"[（(][ぁ-ゖァ-ヺー・ゝゞヽヾ]+[）)]")
# The long-vowel mark ー is phonetic content, so it is NOT in this class.
PUNCT_RE = re.compile(r"[\s、。，．,.!?！？「」『』【】〈〉《》（）()・…‥―—〜~：:；;／/｜|　'\"’”‘“]+")


def normalize(text: str, strip_gloss: bool = True) -> str:
    if strip_gloss:
        text = GLOSS_RE.sub("", text)
    return PUNCT_RE.sub("", text)


def cer(ref: str, hyp: str) -> float:
    """Character error rate via Levenshtein distance."""
    if not ref:
        return 1.0
    m, n = len(ref), len(hyp)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ref[i - 1] != hyp[j - 1]),
            )
        prev = cur
    return prev[n] / m


def speechmatics_batch(wav_path: Path) -> str:
    conf = {
        "type": "transcription",
        "transcription_config": {"language": "ja", "operating_point": "enhanced"},
    }
    r = requests.post(
        "https://asr.api.speechmatics.com/v2/jobs",
        headers={"Authorization": f"Bearer {S_KEY}"},
        files={
            "data_file": (wav_path.name, wav_path.open("rb"), "audio/wav"),
            "config": (None, json.dumps(conf)),
        },
        timeout=120,
    )
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(120):
        time.sleep(5)
        r = requests.get(
            f"https://asr.api.speechmatics.com/v2/jobs/{job}",
            headers={"Authorization": f"Bearer {S_KEY}"},
            timeout=60,
        )
        st = r.json()["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "deleted", "expired"):
            raise RuntimeError(f"job {job} {st}: {r.text}")
    r = requests.get(
        f"https://asr.api.speechmatics.com/v2/jobs/{job}/transcript?format=txt",
        headers={"Authorization": f"Bearer {S_KEY}"},
        timeout=60,
    )
    r.raise_for_status()
    return r.content.decode("utf-8")


def gemini_transcribe(wav_path: Path) -> str:
    audio_b64 = base64.b64encode(wav_path.read_bytes()).decode()
    body = {
        "contents": [
            {
                "parts": [
                    {
                        "text": "この音声を一字一句そのまま文字起こししてください。"
                        "ナレーション本文のみを出力し、注釈や説明は不要です。"
                        "聞き取れない場合は推測せず、聞こえたとおりに書いてください。"
                    },
                    {"inline_data": {"mime_type": "audio/wav", "data": audio_b64}},
                ]
            }
        ],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 8192},
    }
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json=body,
        timeout=300,
    )
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def spoken_subrange(page_norm: str, asr_norm: str) -> tuple[int, int]:
    """Locate which part of the page text the (excerpt) audio covers."""
    sm = difflib.SequenceMatcher(None, page_norm, asr_norm, autojunk=False)
    blocks = [b for b in sm.get_matching_blocks() if b.size >= 4]
    if not blocks:
        return (0, len(page_norm))
    return (blocks[0].a, blocks[-1].a + blocks[-1].size)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    arts = {a["slug"]: a for a in json.loads((ROOT / "corpus" / "articles.json").read_text())}
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())

    results = {}
    for seg_id, spec in picks.items():
        wav = WAV / f"{spec['wav']}"
        secs = [arts[spec["slug"]]["sections"][i] for i in spec["sections"]]
        page_text = "\n".join(s["text"] for s in secs)
        page_norm = normalize(page_text)

        sm_file = OUT / f"{seg_id}.speechmatics.txt"
        gm_file = OUT / f"{seg_id}.gemini.txt"
        if not sm_file.exists():
            print(f"[{seg_id}] speechmatics batch ...", file=sys.stderr)
            sm_file.write_text(speechmatics_batch(wav), encoding="utf-8")
        if not gm_file.exists():
            print(f"[{seg_id}] gemini transcribe ...", file=sys.stderr)
            gm_file.write_text(gemini_transcribe(wav), encoding="utf-8")

        sm_norm = normalize(sm_file.read_text(), strip_gloss=False)
        gm_norm = normalize(gm_file.read_text(), strip_gloss=False)

        a, b = spoken_subrange(page_norm, sm_norm)
        page_sub = page_norm[a:b]
        results[seg_id] = {
            "page_chars": len(page_norm),
            "spoken_range": [a, b],
            "coverage": round((b - a) / len(page_norm), 3),
            "cer_page_vs_sm": round(cer(page_sub, sm_norm), 4),
            "cer_page_vs_gm": round(cer(page_sub, gm_norm), 4),
            "cer_sm_vs_gm": round(cer(sm_norm, gm_norm), 4),
        }
        print(seg_id, results[seg_id])

    (OUT / "verify_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
