#!/usr/bin/env python3
"""exp1 追加 arm `Abat`:Gemini **非 Live** 的 generateContent 逐字轉寫。

為什麼要補這一格:exp1/exp2/exp3/exp4 的「一體式」全部走 Live API,
而 exp5 在同一批音檔上量到 Live 與 generateContent 的抗噪差很多
(M3:Live 0.551 vs batch 0.816~0.880)。若這個差異成立,
`results/stt-matrix.md` 的頭條結論「一體式模型在噪音下會崩潰」
就該限定成「一體式**即時**」——那是完全不同的建議。

exp1 是驗證這件事最乾淨的地方:參考文本來自大阪觀光局頁面,
**不是任何受測引擎產生的**,沒有主場偏差;而且 N4(人聲 8dB)正是
Live 崩到 0.030 的那一格。

輸出欄位對齊 arm A(`input_transcription`),讓 `scripts/score.py` 直接吃。
"""
import argparse
import base64
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
KEY = os.environ["gemini_key"]
MODEL = os.environ.get("GBAT_MODEL", "gemini-3.5-flash")
THINK = os.environ.get("GBAT_THINK", "minimal")
ARM = os.environ.get("GBAT_ARM", "Abat")
RAW = ROOT / "results" / "raw" / ARM
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# 與 exp5 的 Gbat 同一種樸素 prompt,只換語言:要逐字,不要摘要或加工。
PROMPT = ("この音声を一字一句そのまま文字起こししてください。"
          "要約・話者ラベル・タイムコードは付けず、書き起こしのテキストだけを出力してください。")


def run_one(wav: Path) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "audio/wav",
                             "data": base64.b64encode(wav.read_bytes()).decode()}},
        ]}],
        "generationConfig": {"thinkingConfig": {"thinkingLevel": THINK},
                             "temperature": 0.0},
    }
    t0 = time.time()
    r = requests.post(URL, headers={"x-goog-api-key": KEY}, json=body, timeout=900)
    el = time.time() - t0
    r.raise_for_status()
    d = r.json()
    text = "".join(p.get("text", "")
                   for p in d["candidates"][0]["content"]["parts"] if "text" in p)
    return {"input_transcription": text.strip(), "elapsed_sec": round(el, 1),
            "usage": d.get("usageMetadata", {})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conds", default="N0,N4", help="逗號分隔;預設只跑兩端")
    args = ap.parse_args()

    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    segs = picks if isinstance(picks, list) else list(picks)
    targets = [f"{s}__{c}" for s in segs for c in args.conds.split(",")]
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "_meta.json").write_text(json.dumps(
        {"arm": ARM, "model": MODEL, "api": "generateContent",
         "thinkingLevel": THINK, "temperature": 0.0, "prompt": PROMPT,
         "note": "非 Live,一次送整段音訊;無 timestamp。用於檢驗噪音崩潰是否為 Live 特有"},
        ensure_ascii=False, indent=1))

    for stem in targets:
        dst = RAW / f"{stem}.json"
        if dst.exists():
            continue
        wav = COND / f"{stem}.wav"
        if not wav.exists():
            print(f"SKIP {stem}(無音檔)", flush=True)
            continue
        res = None
        for attempt in range(3):
            try:
                res = run_one(wav)
                break
            except Exception as e:
                print(f"ERR {stem} attempt {attempt}: {type(e).__name__} {e}", flush=True)
                time.sleep(20 * (attempt + 1))
        if res is None:
            print(f"FAILED {stem}", flush=True)
            continue
        dst.write_text(json.dumps({
            "arm": ARM, "file": f"{stem}.wav", "model": MODEL,
            "audio_s": round(wav.stat().st_size / (16000 * 2), 1), **res,
        }, ensure_ascii=False, indent=1))
        print(f"  {stem}  {res['elapsed_sec']}s  {len(res['input_transcription'])} 字", flush=True)


if __name__ == "__main__":
    main()
