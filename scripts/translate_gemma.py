#!/usr/bin/env python3
"""案外案:第二跳(譯)換成 Gemma 4,與 gemini-3.5-flash 同料對照。

**為什麼只做第二跳:** 會聽的 Gemma 4 變體(E2B / E4B / 12B Unified,HF 上
tag 是 `any-to-any`)在本環境的任何 API 上都拿不到——AI Studio 與 OpenRouter
都只服務 26B-A4B 與 31B,兩者的 input modality 是 image/text/video,**沒有 audio**
(實測 400:"Audio input modality is not enabled for this model")。
所以第一跳(聽)無法用 API 比,只有第二跳(譯)能比。

輸入用 `Cplus`(SM 即時 + 詞表)的日文定稿逐字稿,與既有的 `Cplus_translate`
(gemini-3.5-flash)是同一批輸入、同一份**凍結的** INTERPRETER_SYSTEM,
唯一變數是譯的模型。

Gemma 4 的坑:它把 thinking 當一般 part 回傳且不一定標 `thought`,
直接抓 parts[0] 會拿到一串英文分析而不是譯文。此處濾掉 thought part,
並且照鐵律 4 送 `thinkingLevel: "minimal"`(實測 Gemma 4 吃這個參數,
預設會燒 493~692 thoughts 譯一句話,延遲 16s → 1.9s)。
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
MODEL = os.environ.get("GEMMA_MODEL", "gemma-4-26b-a4b-it")
ARM_SUFFIX = os.environ.get("GEMMA_SUFFIX", "gemma26")
CONDS = os.environ.get("GEMMA_CONDS", "N0").split(",")


def translate(transcript: str) -> tuple[str, dict, float]:
    body = {
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "contents": [
            {"parts": [{"text": TRANSLATE_USER_TEMPLATE.format(transcript=transcript)}]}
        ],
        "generationConfig": {"temperature": 0.2,
                             "thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    t0 = time.time()
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json=body, timeout=300)
    el = time.time() - t0
    r.raise_for_status()
    d = r.json()
    parts = d["candidates"][0]["content"]["parts"]
    # 濾掉 thought part——Gemma 4 會把推理當一般 part 回傳
    text = "".join(p.get("text", "") for p in parts
                   if "text" in p and not p.get("thought"))
    return text.strip(), d.get("usageMetadata", {}), el


def main() -> None:
    for arm in sys.argv[1:] or ["Cplus"]:
        indir = ROOT / "results" / "raw" / arm
        outdir = ROOT / "results" / "raw" / f"{arm}_translate_{ARM_SUFFIX}"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "_meta.json").write_text(json.dumps(
            {"arm": arm, "hop": "translate", "model": MODEL,
             "thinkingLevel": "minimal", "temperature": 0.2,
             "prompt": "INTERPRETER_SYSTEM(凍結,與 gemini 對照組逐字相同)",
             "note": "Gemma 4 無音訊模態,只能做第二跳"},
            ensure_ascii=False, indent=1))
        for f in sorted(indir.glob("*.json")):
            if "__" not in f.stem:
                continue
            if f.stem.split("__")[1] not in CONDS:
                continue
            out = outdir / f.name
            if out.exists():
                continue
            d = json.loads(f.read_text())
            for attempt in range(3):
                try:
                    text, usage, el = translate(d["transcript"])
                    break
                except Exception as e:
                    print(f"ERR {f.stem} attempt {attempt}: {type(e).__name__} {e}",
                          flush=True)
                    text = None
                    time.sleep(15 * (attempt + 1))
            if text is None:
                print(f"FAILED {f.stem}", flush=True)
                continue
            out.write_text(json.dumps(
                {"arm": arm, "file": d["file"], "model": MODEL,
                 "translation": text, "elapsed_sec": round(el, 1), "usage": usage},
                ensure_ascii=False, indent=1))
            print(f"  {f.stem}  {el:.1f}s  {len(text)} 字  "
                  f"thoughts={usage.get('thoughtsTokenCount', 0)} "
                  f"out={usage.get('candidatesTokenCount')}", flush=True)


if __name__ == "__main__":
    main()
