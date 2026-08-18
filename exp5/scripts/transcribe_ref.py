#!/usr/bin/env python3
"""exp5 正解建置步驟 1:每段三路獨立轉寫(沿用 exp2 的協定,不改判準)。

  - Speechmatics batch, language=cmn_en (enhanced)
  - gemini-3.5-flash   (逐字中英夾雜轉寫 prompt)
  - gemini-3.6-flash   (同 prompt,不同世代)

**Breeze 刻意不列入正解來源。** 它是本次的受測對象,拿它的輸出當正解再去評它
就是自我評分(handoff-v6 §4)。所以 exp5 的正解是「兩路 Gemini + SM 第三意見」,
與 exp2 相同。

與 exp2 的一個差異:SM 這裡用 cmn_en 雙語包而非 cmn 單語包。exp2 之所以不讓
SM 對英文拼寫投票,是因為單語 cmn 在這個維度系統性失能(COVID-19 → CoffeeNight);
雙語包沒有這個問題,但為了不改動已凍結的判準,**投票權仍然只給兩路 Gemini**,
SM 一樣只當第三意見存檔。

輸出 exp5/corpus/verify/{seg}.{engine}.txt
"""
import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
WAV = ROOT / "corpus" / "wav"
OUT = ROOT / "corpus" / "verify"
S_KEY = os.environ["s_key"]
G_KEY = os.environ["gemini_key"]

PROMPT = (
    "請將這段台灣的科技訪談 podcast 錄音逐字轉寫。\n"
    "- 講者以中文對談,夾雜大量英文術語(例如 server、cloud、API)。\n"
    "- 可能有兩位以上講者交談,不必標註誰在說話,照時間順序寫下來即可。\n"
    "- 中文寫繁體中文,英文術語保留英文原文拼寫,不要翻成中文。\n"
    "- 逐字轉寫,保留口語(這個、那、對不對),不要改寫、不要摘要。\n"
    "- 只輸出轉寫文字,不要任何說明。"
)


def sm_batch(wav: Path) -> str:
    conf = {"type": "transcription",
            "transcription_config": {"language": "cmn_en", "operating_point": "enhanced"}}
    r = requests.post("https://asr.api.speechmatics.com/v2/jobs",
                      headers={"Authorization": f"Bearer {S_KEY}"},
                      files={"data_file": (wav.name, wav.open("rb"), "audio/wav"),
                             "config": (None, json.dumps(conf))}, timeout=180)
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(180):
        time.sleep(5)
        st = requests.get(f"https://asr.api.speechmatics.com/v2/jobs/{job}",
                          headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60).json()["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "deleted"):
            raise RuntimeError(st)
    r = requests.get(f"https://asr.api.speechmatics.com/v2/jobs/{job}/transcript?format=txt",
                     headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60)
    r.raise_for_status()
    return r.content.decode("utf-8")


def gemini_tx(wav: Path, model: str) -> str:
    b64 = base64.b64encode(wav.read_bytes()).decode()
    body = {"contents": [{"parts": [{"text": PROMPT},
                                    {"inline_data": {"mime_type": "audio/wav", "data": b64}}]}],
            "generationConfig": {"temperature": 0.0, "maxOutputTokens": 16384}}
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
        json=body, timeout=600)
    r.raise_for_status()
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    segs = [p["seg"] for p in json.loads((ROOT / "corpus" / "picks.json").read_text())]
    for seg in segs:
        wav = WAV / f"{seg}.wav"
        for name, fn in [("sm", lambda w: sm_batch(w)),
                         ("gm35", lambda w: gemini_tx(w, "gemini-3.5-flash")),
                         ("gm36", lambda w: gemini_tx(w, "gemini-3.6-flash"))]:
            f = OUT / f"{seg}.{name}.txt"
            if f.exists():
                continue
            print(f"{seg}.{name} ...", file=sys.stderr)
            f.write_text(fn(wav), encoding="utf-8")
            print(f"{seg}.{name}: {len(f.read_text())} ch", file=sys.stderr)


if __name__ == "__main__":
    main()
