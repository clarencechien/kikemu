#!/usr/bin/env python3
"""挑選 exp5 的 5 分鐘切片視窗:整集轉寫 → 滑動視窗找術語最密的一段。

為什麼不隨便取中段:領域外對照要能跟 exp2 比,術語密度就得在同一個量級
(exp2 標尺 S1 10.4 / S2 4.0 / S3 4.8 個拉丁段/分鐘)。播客一集 60~90 分鐘裡
密度分佈很不均——閒聊段落接近 0,講實作的段落可以到 8 以上。

**視窗必須在任何 arm 跑之前決定**,而且是由這支腳本用固定規則選出來的,
不是聽過覺得哪段好聽就選哪段。(exp2 的 S3 視窗也是這樣從 300-600 移到
900-1200 的,見 exp2/scripts/prep_audio.py 的註解。)

輸出 exp5/corpus/picks.json,欄位含視窗起訖、實測密度、整集密度分佈摘要。
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

S_KEY = os.environ["s_key"]
BASE = "https://asr.api.speechmatics.com/v2"
ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("EXP5_CACHE", "/tmp/exp5-cache"))
CACHE.mkdir(parents=True, exist_ok=True)

WIN = 300.0      # 切片長度(秒),與 exp2 一致
STEP = 30.0      # 滑動步長
SKIP_HEAD = 300  # 前 5 分鐘通常是開場與贊助,不納入
SKIP_TAIL = 180  # 結尾同理

LATIN = re.compile(r"^[A-Za-z][A-Za-z0-9\-\.\+#]*$")

# ML/AI 語域守門。exp5 存在的理由就是「換一個模型沒看過的語域」,而 Breeze 的
# 污染來源是 ML 課程——所以視窗裡如果在聊 LLM,就算術語再密也不能用。
# 實測踩過:只取「最密視窗」時,T2/T3 都落在受訪者談 ChatGPT / 思維鏈 / scaling
# 的段落(AI 詞 12 與 22 次),等於把語域又繞回去了。
ML_TERM = re.compile(
    r"^(?:llm|gpt|chatgpt|deepseek|prompt|prompts|token|tokens|inference|agent|agents|"
    r"embedding|embeddings|transformer|fine-?tune|fine-?tuned|pre-?trained|pretrained|"
    r"scaling|rag|ai|ml|model|models|training|neural)$", re.I)
ML_CJK = re.compile(r"模型|機器學習|深度學習|人工智慧|思維鏈|聊天機器人|大語言|語言模型")
MAX_ML_PER_WINDOW = 3   # 5 分鐘視窗內最多容忍幾個 ML/AI 詞(T1 的乾淨視窗是 1)


def transcribe_full(mp3: Path, tag: str) -> list:
    """整集轉寫,回傳 [(start_sec, word)];結果快取,不重複付費。"""
    cache = CACHE / f"{tag}.words.json"
    if cache.exists():
        return json.loads(cache.read_text())
    conf = {
        "type": "transcription",
        "transcription_config": {"language": "cmn_en", "operating_point": "enhanced"},
    }
    with open(mp3, "rb") as f:
        r = requests.post(
            f"{BASE}/jobs",
            headers={"Authorization": f"Bearer {S_KEY}"},
            files={"data_file": (mp3.name, f, "audio/mpeg"),
                   "config": (None, json.dumps(conf))},
            timeout=300,
        )
    r.raise_for_status()
    job = r.json()["id"]
    print(f"  {tag}: job {job} 送出,等待轉寫…", flush=True)
    for _ in range(360):
        time.sleep(10)
        st = requests.get(f"{BASE}/jobs/{job}", headers={"Authorization": f"Bearer {S_KEY}"},
                          timeout=60).json()["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "expired"):
            raise RuntimeError(f"{tag}: SM {st}")
    else:
        raise RuntimeError(f"{tag}: 逾時")
    d = requests.get(f"{BASE}/jobs/{job}/transcript?format=json-v2",
                     headers={"Authorization": f"Bearer {S_KEY}"}, timeout=300).json()
    words = [(it["start_time"], it["alternatives"][0]["content"])
             for it in d["results"] if it.get("type") == "word" and it.get("alternatives")]
    cache.write_text(json.dumps(words, ensure_ascii=False))
    return words


def latin_runs(words):
    """回傳每個拉丁段落的起始秒數。連續的拉丁詞算一段(『live stream』= 1 段),
       與 probe_density.py 及 exp2 的算法一致。"""
    out, prev_latin = [], False
    for t, w in words:
        is_latin = bool(LATIN.match(w))
        if is_latin and not prev_latin:
            out.append(t)
        prev_latin = is_latin
    return out


def ml_hits(words, t0, t1):
    return sum(1 for t, w in words
               if t0 <= t < t1 and (ML_TERM.match(w) or ML_CJK.search(w)))


def best_window(runs, dur, words):
    """在**通過 ML 語域守門**的視窗裡取密度最高的那個。
       回傳 (起點, 段數, 該視窗 ML 詞數) 與整條曲線(含每格的 ML 詞數,可稽核)。"""
    lo, hi = SKIP_HEAD, max(SKIP_HEAD + WIN, dur - SKIP_TAIL - WIN)
    best = (None, -1, None)
    curve = []
    t = lo
    while t <= hi:
        n = sum(1 for r in runs if t <= r < t + WIN)
        m = ml_hits(words, t, t + WIN)
        curve.append((round(t), n, m))
        if m <= MAX_ML_PER_WINDOW and n > best[1]:
            best = (t, n, m)
        t += STEP
    if best[0] is None:
        raise RuntimeError("整集沒有任何視窗通過 ML 語域守門——這一集不適合當領域外語料")
    return best, curve


if __name__ == "__main__":
    cands = json.loads(Path(sys.argv[1]).read_text())
    picks = []
    for c in cands:
        mp3 = Path(c["mp3"])
        words = transcribe_full(mp3, c["seg"])
        dur = words[-1][0] if words else 0
        runs = latin_runs(words)
        (start, n, mlw), curve = best_window(runs, dur, words)
        dens_all = len(runs) / (dur / 60) if dur else 0
        print(f"\n{c['seg']} ({c['name']})")
        print(f"  整集 {dur/60:.0f} 分,拉丁段 {len(runs)} 個 = {dens_all:.1f}/分鐘")
        print(f"  選中視窗:{int(start)}s–{int(start+WIN)}s,{n} 段 = {n/5:.1f}/分鐘"
              f"(ML 詞 {mlw} 個,門檻 ≤{MAX_ML_PER_WINDOW})")
        picks.append({**{k: v for k, v in c.items() if k != "mp3"},
                      "start": int(start), "dur": int(WIN),
                      "window_runs": n, "window_density_per_min": round(n / 5, 1),
                      "window_ml_terms": mlw, "ml_gate": MAX_ML_PER_WINDOW,
                      "episode_minutes": round(dur / 60, 1),
                      "episode_density_per_min": round(dens_all, 1),
                      "density_curve": curve})
    out = ROOT / "corpus" / "picks.json"
    out.write_text(json.dumps(picks, ensure_ascii=False, indent=1))
    print(f"\n寫出 {out}")
