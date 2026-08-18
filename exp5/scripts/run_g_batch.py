#!/usr/bin/env python3
"""exp5 arm Gbat:Gemini **非 Live** 的 generateContent 一次丟整段音訊。

為什麼要有這個 arm:arm G 用的是 Live API,它按 1× 實時推流,5 分鐘音檔就要跑
5 分鐘——那是「即時」的代價,不是 Gemini 的速度上限。若情境不需要即時、
也不需要 timestamp,該比的是 generateContent,而不是 Live。
在量到這個 arm 之前,「不即時就用 Gemini 比較快又省」是**未驗證**的說法。

輸出欄位對齊其他 arm(score.py 讀 transcript),並記下 wall time 與 usage,
成本與速度都能直接對回原始檔。
"""
import argparse
import base64
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
ARM = os.environ.get("GBAT_ARM", "Gbat")
RAW = ROOT / "results" / "raw" / ARM
KEY = os.environ["gemini_key"]
# 用 3.5-flash:這是 repo 已核實過牌價的那一級($1.50/M in、$9.00/M out,
# 2026-08-14 查),成本敘述才對得回原始檔。Live arm 用的 3.1-flash-live-preview
# 沒有 generateContent 端點。
MODEL = os.environ.get("GBAT_MODEL", "gemini-3.5-flash")
# 3.7-flash 不吃 MINIMAL(400 "Thinking level MINIMAL is not supported for this
# model"),所以逐台機器指定;能用 minimal 的一律用 minimal(CLAUDE.md 鐵律 4)。
THINK = os.environ.get("GBAT_THINK", "minimal")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"

# 刻意樸素:只要求逐字、保留原樣的英文詞。不要求繁簡、不要求標點風格,
# 免得 prompt 本身變成一個沒被控制的變因(arm G 也沒給這類指示)。
PROMPT = ("逐字轉寫這段音訊。說話者混用中文與英文,英文詞請原樣保留,不要翻譯。"
          "不要摘要、不要加說話者標記、不要加時間碼,只輸出轉寫文字。")


def run_one(wav: Path) -> dict:
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": PROMPT},
            {"inline_data": {"mime_type": "audio/wav",
                             "data": base64.b64encode(wav.read_bytes()).decode()}},
        ]}],
        # 機械性任務 → minimal(CLAUDE.md 鐵律 4)
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
    return {"transcript": text.strip(), "elapsed_sec": round(el, 1),
            "usage": d.get("usageMetadata", {})}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=None)
    args = ap.parse_args()

    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    targets = [f"{p['seg']}__{c}" for p in picks for c in ("M0", "M3")]
    if args.only:
        targets = [t for t in targets if t in args.only]
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "_meta.json").write_text(json.dumps(
        {"arm": ARM, "model": MODEL, "api": "generateContent",
         "thinkingLevel": THINK, "temperature": 0.0, "prompt": PROMPT,
         "note": "非 Live,一次送整段音訊;無 timestamp"},
        ensure_ascii=False, indent=1))

    for stem in targets:
        dst = RAW / f"{stem}.json"
        if dst.exists():
            continue
        wav = COND / f"{stem}.wav"
        for attempt in range(3):
            try:
                res = run_one(wav)
                break
            except Exception as e:  # 429/5xx 退避重試,與其他 arm 同一套做法
                print(f"ERR {stem} attempt {attempt}: {type(e).__name__} {e}", flush=True)
                res = None
                time.sleep(20 * (attempt + 1))
        if res is None:
            print(f"FAILED {stem}", flush=True)
            continue
        dst.write_text(json.dumps({
            "arm": ARM, "file": f"{stem}.wav", "model": MODEL,
            "audio_s": round(wav.stat().st_size / (16000 * 2), 1),
            **res,
        }, ensure_ascii=False, indent=1))
        u = res["usage"]
        print(f"  {stem}  {res['elapsed_sec']}s  {len(res['transcript'])} 字  "
              f"in={u.get('promptTokenCount')} out={u.get('candidatesTokenCount')}",
              flush=True)


if __name__ == "__main__":
    main()
