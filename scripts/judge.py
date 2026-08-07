#!/usr/bin/env python3
"""Adequacy judging panel: 5 vendors via OpenRouter, blind, temperature 0.

Each judge sees the VERIFIED Japanese reference (not any ASR output) and one
candidate zh-TW translation, without knowing which arm produced it.
Scores 1-5 adequacy + 1-5 Taiwan-locale fit. Raw responses stored.
Conditions judged: N0 (clean) and N3 (peak crowd) by default.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
REF = ROOT / "corpus" / "reference"
OR_KEY = os.environ["or_key"]

JUDGES = [
    "anthropic/claude-sonnet-5",
    "openai/gpt-5.6-terra",
    "google/gemini-3.6-flash",
    "qwen/qwen3.7-plus",
    "mistralai/mistral-medium-3-5",
]
CONDS = ["N0", "N3"]
ARMS = ["A", "C", "Cplus"]

PROMPT = """你是翻譯品質評審。以下是一段日語導覽解說的正確原文,以及一份翻譯成台灣繁體中文的候選譯文(翻譯來源是語音辨識結果,可能含辨識錯誤)。

請就兩個面向各給 1-5 分(整數):
1. adequacy:譯文傳達原文資訊的完整與正確程度(5=完整正確,1=大量錯漏)
2. tw_locale:譯文是否符合台灣用語習慣、使用正體中文(5=完全道地,1=明顯中國大陸用語或簡體字)

只輸出 JSON:{{"adequacy": n, "tw_locale": n, "reason": "一句話"}}

## 日語原文
{ref}

## 候選譯文
{zh}
"""


def judge(model: str, ref: str, zh: str) -> dict:
    r = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OR_KEY}"},
        json={
            "model": model,
            "messages": [{"role": "user", "content": PROMPT.format(ref=ref, zh=zh)}],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        },
        timeout=180,
    )
    r.raise_for_status()
    return r.json()


def get_zh(arm: str, seg: str, cond: str) -> str | None:
    if arm == "A":
        f = RES / "raw" / "A" / f"{seg}__{cond}.json"
        return json.loads(f.read_text())["translation"] if f.exists() else None
    f = RES / "raw" / f"{arm}_translate" / f"{seg}__{cond}.json"
    return json.loads(f.read_text())["translation"] if f.exists() else None


def main():
    outdir = RES / "raw" / "judge"
    outdir.mkdir(parents=True, exist_ok=True)
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    scores = []
    for seg in picks:
        ref = (REF / f"{seg}.txt").read_text()
        for cond in CONDS:
            for arm in ARMS:
                zh = get_zh(arm, seg, cond)
                if not zh:
                    print(f"missing zh {arm} {seg} {cond}", file=sys.stderr)
                    continue
                for model in JUDGES:
                    tag = f"{seg}__{cond}__{arm}__{model.replace('/', '_')}"
                    raw_f = outdir / f"{tag}.json"
                    if raw_f.exists():
                        resp = json.loads(raw_f.read_text())
                    else:
                        try:
                            resp = judge(model, ref, zh)
                        except Exception as e:
                            print(f"ERR {tag}: {e}", file=sys.stderr)
                            continue
                        raw_f.write_text(json.dumps(resp, ensure_ascii=False))
                        time.sleep(0.5)
                    try:
                        content = resp["choices"][0]["message"]["content"]
                        content = content[content.index("{") : content.rindex("}") + 1]
                        j = json.loads(content)
                        scores.append(
                            {"seg": seg, "cond": cond, "arm": arm, "judge": model,
                             "adequacy": int(j["adequacy"]), "tw_locale": int(j["tw_locale"])}
                        )
                    except Exception as e:
                        print(f"PARSE ERR {tag}: {e}", file=sys.stderr)
    (RES / "judge_scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1))
    print(f"judged {len(scores)} (arm,seg,cond,judge) tuples")


if __name__ == "__main__":
    main()
