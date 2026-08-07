#!/usr/bin/env python3
"""Extract per-segment proper-noun lists (the primary metric's ground truth).

Protocol:
  - Gemini extracts proper nouns (deity/person/shrine/rite/place/era/brand
    names) from the VERIFIED reference transcript, with the raw page text
    (which carries official reading glosses) as auxiliary context.
  - Each candidate's `surface` MUST appear verbatim in the reference;
    violations are dropped (logged).
  - `alternates` (official gloss reading, standard kanji<->kana orthography)
    are frozen here, BEFORE any arm output is seen, and never edited after.
  - Prompt and raw model responses are stored under corpus/nouns/raw/.
Scoring later counts a hit if the arm's transcript contains the surface OR
any alternate (reading-tolerant rule); a strict surface-only number is also
reported.
"""
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "corpus" / "reference"
OUT = ROOT / "corpus" / "nouns"
G_KEY = os.environ["gemini_key"]
MODEL = "gemini-3.5-flash"

PROMPT = """あなたは音声認識評価のためのアノテーターです。
以下の「参照テキスト」(観光ガイド音声の逐字稿)から、固有名詞・専門語を抽出してください。

対象: 神名、人名、神社名・施設名、神事・祭事名、地名、時代・年号、企業・ブランド名、専門用語(茶道用語など)。
一般名詞や日常語は含めない。

各項目について:
- "surface": 参照テキストに**一字一句そのまま**現れる表記(これが正式表記)
- "reading": 読み(全角カタカナ)。「ページ原文」の括弧内読み仮名があればそれを最優先で使う
- "alternates": 同じ語の標準的な別表記(漢字表記⇔仮名表記、送り仮名違いなど)。存在するものだけ。推測で作らない

15〜30項目。JSONの配列のみを出力:
[{"surface": "...", "reading": "...", "alternates": ["..."]}]

## 参照テキスト
{ref}

## ページ原文(読み仮名の典拠)
{page}
"""


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)
    arts = {a["slug"]: a for a in json.loads((ROOT / "corpus" / "articles.json").read_text())}
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())

    nouns = {}
    for seg_id, spec in picks.items():
        ref = (REF / f"{seg_id}.txt").read_text()
        page = "\n".join(
            arts[spec["slug"]]["sections"][i]["header"]
            + "\n"
            + arts[spec["slug"]]["sections"][i]["text"]
            for i in spec["sections"]
        )
        raw_file = OUT / "raw" / f"{seg_id}.json"
        if raw_file.exists():
            resp = json.loads(raw_file.read_text())
        else:
            prompt = PROMPT.replace("{ref}", ref).replace("{page}", page)
            body = {
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": 0.0,
                    "responseMimeType": "application/json",
                },
            }
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
                json=body,
                timeout=300,
            )
            r.raise_for_status()
            resp = r.json()
            raw_file.write_text(json.dumps(resp, ensure_ascii=False, indent=1))
        text = resp["candidates"][0]["content"]["parts"][0]["text"]
        items = json.loads(text)
        kept, dropped = [], []
        seen = set()
        for it in items:
            surf = it["surface"]
            if surf in ref and surf not in seen:
                seen.add(surf)
                kept.append(
                    {
                        "surface": surf,
                        "reading": it.get("reading", ""),
                        "alternates": [a for a in it.get("alternates", []) if a and a != surf],
                    }
                )
            else:
                dropped.append(surf)
        # Curation rules (mechanical, applied uniformly):
        # C1. Generic standalone words are not proper nouns.
        STOP = {"日本", "神社", "ミコト", "参道", "神事", "幕府", "公園", "井戸", "街道", "和歌"}
        kept2 = [it for it in kept if it["surface"] not in STOP]
        dropped += [it["surface"] for it in kept if it["surface"] in STOP]
        # C2. Title-concatenation artifacts: a surface equal to the whole
        #     normalized section title that only occurs at position 0.
        kept3 = []
        for it in kept2:
            s = it["surface"]
            if len(s) >= 8 and ref.startswith(s) and ref.count(s) == 1 and any(
                o["surface"] != s and o["surface"] in s for o in kept2
            ):
                dropped.append(s + "(title-concat)")
            else:
                kept3.append(it)
        # C3. Substring dedup: keep maximal units only (a hit on the longer
        #     string would auto-hit the substring and double-count).
        surfaces = [it["surface"] for it in kept3]
        final = [
            it
            for it in kept3
            if not any(o != it["surface"] and it["surface"] in o for o in surfaces)
        ]
        dropped += [
            it["surface"] + "(substr)" for it in kept3 if it not in final
        ]
        nouns[seg_id] = final
        print(f"{seg_id}: kept={len(kept)} dropped={dropped}", file=sys.stderr)

    (OUT / "nouns.json").write_text(
        json.dumps(nouns, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
