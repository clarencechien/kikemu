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


def best_window(runs, dur):
    lo, hi = SKIP_HEAD, max(SKIP_HEAD + WIN, dur - SKIP_TAIL - WIN)
    best = (None, -1)
    curve = []
    t = lo
    while t <= hi:
        n = sum(1 for r in runs if t <= r < t + WIN)
        curve.append((round(t), n))
        if n > best[1]:
            best = (t, n)
        t += STEP
    return best, curve


if __name__ == "__main__":
    cands = json.loads(Path(sys.argv[1]).read_text())
    picks = []
    for c in cands:
        mp3 = Path(c["mp3"])
        words = transcribe_full(mp3, c["seg"])
        dur = words[-1][0] if words else 0
        runs = latin_runs(words)
        (start, n), curve = best_window(runs, dur)
        dens_all = len(runs) / (dur / 60) if dur else 0
        print(f"\n{c['seg']} ({c['name']})")
        print(f"  整集 {dur/60:.0f} 分,拉丁段 {len(runs)} 個 = {dens_all:.1f}/分鐘")
        print(f"  最密的 5 分鐘視窗:{int(start)}s–{int(start+WIN)}s,{n} 段 = {n/5:.1f}/分鐘")
        picks.append({**{k: v for k, v in c.items() if k != "mp3"},
                      "start": int(start), "dur": int(WIN),
                      "window_runs": n, "window_density_per_min": round(n / 5, 1),
                      "episode_minutes": round(dur / 60, 1),
                      "episode_density_per_min": round(dens_all, 1),
                      "density_curve": curve})
    out = ROOT / "corpus" / "picks.json"
    out.write_text(json.dumps(picks, ensure_ascii=False, indent=1))
    print(f"\n寫出 {out}")
