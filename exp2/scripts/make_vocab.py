#!/usr/bin/env python3
"""Build the two vocabulary sources for the X-domain / X-slides arms.

domain.json  — generic ML/AI glossary terms from Google's public ML glossary
               page (765 headings). NEVER sees the slides or any transcript.
slides_S*.json — English terms extracted from THIS lecture's official slide
               PDF text (per segment). This is the "会前準備" arm; overlap
               with the reference is the product scenario, not leakage
               (handoff-v2 §4).

Both emit Speechmatics additional_vocab entries: {"content": term}.
Format rules: 1-4 words, letters/digits/hyphen only, deduped case-insensitively,
max 950 entries (SM limit 1000).
"""
import json
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus" / "vocab"
SP = Path("/tmp/claude-0/-home-user-kikemu/5605051a-5678-5cbe-839f-18cc17c6d816/scratchpad")

TERM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9\-/. ]{1,40}$")

SLIDES = {"S1": "self_v7.pdf.txt", "S2": "gan_v10.pdf.txt", "S3": "0223_intro_gai.pdf.txt"}

G_KEY = None  # set in main


def clean_terms(terms):
    seen, out = set(), []
    for t in terms:
        t = t.strip().strip(".,;:()[]")
        if len(t) < 2 or not TERM_RE.match(t) or len(t.split()) > 4:
            continue
        k = t.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(t)
    return out[:950]


def build_domain():
    html = (SP / "mlglossary.html").read_text(encoding="utf-8")
    soup = BeautifulSoup(html, "html.parser")
    terms = [h.get_text(" ", strip=True) for h in soup.find_all(["h2", "h3"]) if h.get("id")]
    terms = [t for t in terms if t not in ("Page Summary",) and len(t) > 1]
    entries = [{"content": t} for t in clean_terms(terms)]
    (OUT / "domain.json").write_text(json.dumps(entries, indent=1))
    print(f"domain: {len(entries)} entries")


def build_slides(seg: str, txt_name: str):
    import os
    text = (ROOT / "corpus" / "slides" / txt_name).read_text(encoding="utf-8")
    prompt = (
        "以下是一份機器學習課程投影片的文字內容。抽出裡面出現的英文術語"
        "(模型名、架構名、訓練術語、縮寫),供語音辨識的自訂詞表使用。\n"
        "只輸出 JSON 陣列,每項是一個英文術語字串。不要中文詞。最大 200 項。\n\n" + text[:15000]
    )
    r = requests.post(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent",
        headers={"x-goog-api-key": os.environ["gemini_key"], "Content-Type": "application/json"},
        json={"contents": [{"parts": [{"text": prompt}]}],
              "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"}},
        timeout=300)
    r.raise_for_status()
    raw = r.json()
    (OUT / f"slides_{seg}.raw.json").write_text(json.dumps(raw, ensure_ascii=False))
    text_out = raw["candidates"][0]["content"]["parts"][0]["text"]
    try:
        terms = json.loads(text_out)
    except json.JSONDecodeError:
        # tolerate malformed JSON: pull out the quoted strings directly
        terms = re.findall(r'"([^"\n]{2,50})"', text_out)
    entries = [{"content": t} for t in clean_terms(terms)]
    (OUT / f"slides_{seg}.json").write_text(json.dumps(entries, indent=1))
    print(f"slides_{seg}: {len(entries)} entries")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    build_domain()
    for seg, txt in SLIDES.items():
        if not (OUT / f"slides_{seg}.json").exists():
            build_slides(seg, txt)


if __name__ == "__main__":
    main()
