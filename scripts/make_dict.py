#!/usr/bin/env python3
"""Generate per-domain custom dictionaries for arm C+ (and the product's
"auto context pack by location" hypothesis).

Leakage control: the dictionary is generated ONLY from Japanese Wikipedia
pages named after the tour stops / city — NEVER from the osaka-info.jp pages
that double as our reference transcripts. If a term the narrator uses is
missing from Wikipedia, it stays missing; that is part of what we measure.

Output:
  corpus/dict/wiki/<title>.txt          fetched source text (audit trail)
  corpus/dict/raw/<domain>.json         raw Gemini response
  corpus/dict/speechmatics_vocab.json   {domain: [{content, sounds_like}]}
  corpus/dict/validation.json           format-check results
"""
import json
import os
import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus" / "dict"
G_KEY = os.environ["gemini_key"]
MODEL = "gemini-3.5-flash"

# Mechanical page-selection rule: city page + pages named after tour stops.
PAGES = {
    "higashiosaka": ["東大阪市", "石切劔箭神社", "枚岡神社"],
    "sakai": ["堺市", "菅原神社 (堺市)", "ザビエル公園", "宿院頓宮", "千利休"],
    "ikeda": ["池田市", "呉服神社", "池田駅 (大阪府)", "小林一三"],
}

KANA_RE = re.compile(r"^[ぁ-ゖァ-ヺー]+$")

PROMPT = """あなたは音声認識用のカスタム語彙(custom dictionary)を作るアシスタントです。
以下は、ある観光ルートの訪問先に関するWikipedia記事の本文です。
観光ガイドの音声認識で誤認識されやすい固有名詞・専門語を抽出し、
音声認識エンジンに登録する語彙リストを作ってください。

対象: 神名、人名、神社・施設名、神事・祭事名、地名、駅名、社名、時代・年号、茶道等の専門用語。
各項目:
- "content": 正式表記(記事中の表記)
- "sounds_like": 読みの配列(全角ひらがな。読みが複数あれば複数)

必ず含めるもの:
- 各Wikipedia記事のタイトル主題そのもの(神社名・公園名・駅名・人名など)
- 記事に登場する境内施設名(〜殿、〜宮など)、神名、神事・祭事名、関連人物名

一般語は含めない。最大150項目。JSONの配列のみ出力:
[{"content": "...", "sounds_like": ["..."]}]

## Wikipedia記事
{src}
"""


def fetch_wiki(title: str) -> str | None:
    r = requests.get(
        "https://ja.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "prop": "extracts",
            "explaintext": 1,
            "format": "json",
            "titles": title,
            "redirects": 1,
        },
        headers={"User-Agent": "kikemu-eval/0.1 (private evaluation)"},
        timeout=60,
    )
    r.raise_for_status()
    pages = r.json()["query"]["pages"]
    page = next(iter(pages.values()))
    return page.get("extract") or None


def gemini(prompt: str) -> dict:
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json={
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.0, "responseMimeType": "application/json"},
        },
        timeout=600,
    )
    r.raise_for_status()
    return r.json()


def main():
    (OUT / "wiki").mkdir(parents=True, exist_ok=True)
    (OUT / "raw").mkdir(exist_ok=True)
    vocab = {}
    validation = {}
    for domain, titles in PAGES.items():
        srcs = []
        for t in titles:
            f = OUT / "wiki" / f"{t.replace(' ', '_').replace('/', '_')}.txt"
            if f.exists():
                text = f.read_text()
            else:
                text = fetch_wiki(t) or ""
                f.write_text(text, encoding="utf-8")
                time.sleep(1)
            print(f"{domain}: {t} -> {len(text)} ch")
            if text:
                srcs.append(f"### {t}\n{text[:20000]}")
        raw_f = OUT / "raw" / f"{domain}.json"
        if raw_f.exists():
            resp = json.loads(raw_f.read_text())
        else:
            resp = gemini(PROMPT.replace("{src}", "\n\n".join(srcs)))
            raw_f.write_text(json.dumps(resp, ensure_ascii=False, indent=1))
        items = json.loads(resp["candidates"][0]["content"]["parts"][0]["text"])
        ok, flagged = [], []
        seen = set()
        for it in items:
            c = it.get("content", "").strip()
            sl = [s.strip() for s in it.get("sounds_like", []) if s.strip()]
            if not c or c in seen:
                continue
            seen.add(c)
            bad = [s for s in sl if not KANA_RE.match(s)]
            long = [s for s in sl if len(s) > 6]
            if bad:
                flagged.append({"content": c, "invalid_sounds_like": bad})
            sl = [s for s in sl if KANA_RE.match(s)]
            entry = {"content": c}
            if sl:
                entry["sounds_like"] = sl
            ok.append(entry)
            if long:
                flagged.append({"content": c, "over_6_kana": long})
        vocab[domain] = ok
        validation[domain] = {"entries": len(ok), "flagged": flagged}
        print(f"{domain}: {len(ok)} entries, {len(flagged)} flags")

    (OUT / "speechmatics_vocab.json").write_text(
        json.dumps(vocab, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (OUT / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=1), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
