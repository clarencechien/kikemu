#!/usr/bin/env python3
"""handoff-v10 S1/S2/S3:ElevenLabs Scribe v2 批次轉寫。

**為什麼有這格:** Scribe 是做字幕的業界預設(SubtitleAI、Outtake 直接用),
但**本專案從沒跑過**——它與 Deepgram 並列「投報率最高的未執行實驗」。

三個語料共用這支,靠 `--corpus` 切換:

    exp1   日文導覽 6 段 × N0–N4   對照 Cbplus(SM 批次 + 詞表)
    exp5   中英夾雜 3 段 × M0/M3   對照 Xbat_bi 0.699 / Breeze 0.845
    zh     純中文 20 秒視窗 × 8    對照 Breeze CER 0.198 / SM 0.206

**踩過的坑(probe 時發現,寫在這裡免得下一個人再踩):**

1. **`keyterms` 必須用「重複的表單欄位」送,不能送 JSON 字串。**
   送 `json.dumps(list)` 會被當成**一個**超長關鍵詞,回
   `400 All keywords must be less than 50 characters`。
2. 錯誤訊息裡的參數名是 `keywords`,但請求要送 `keyterms`——名字對不上,
   照文件送就對。
3. `language_code` 送 ISO-639-1(`ja`),回傳的是 ISO-639-3(`jpn`)。

用法:
    python3 scripts/run_scribe_batch.py --corpus exp1 --probe
    python3 scripts/run_scribe_batch.py --corpus exp1
    python3 scripts/run_scribe_batch.py --corpus exp1 --no-keyterms --arm Sbat_ja_nokt
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
URL = "https://api.elevenlabs.io/v1/speech-to-text"
MODEL = "scribe_v2"
# 2026-08-20 查。抄價附日期(鐵律 6)。
RATE_USD_PER_HOUR = {"base": 0.22, "keyterms": 0.05}
# 即時的官方上限是 50 詞 × 20 字元。**批次實測吃得下完整的 166 條**
# (50/100/166 都回 200),所以 50 這個預設純粹是為了與即時那格(S4)對齊,
# 不是 API 限制。`--full-keyterms` 送完整詞表。
KEYTERM_CAP = 50

CORPORA = {
    "exp1": {"dir": ROOT / "corpus/conditions", "lang": "ja",
             "conds": ["N0", "N1", "N2", "N3", "N4"],
             "picks": ROOT / "corpus/picks.json",
             "dict": ROOT / "corpus/dict/speechmatics_vocab.json",
             "out": ROOT / "results/raw"},
    "exp5": {"dir": ROOT / "exp5/corpus/conditions", "lang": "zh",
             "conds": ["M0", "M3"],
             "picks": ROOT / "exp5/corpus/picks.json",
             "dict": None,
             "out": ROOT / "exp5/results/raw"},
    "zh": {"dir": ROOT / "exp5/corpus/zh_windows", "lang": "zh",
           "conds": None, "picks": None, "dict": None,
           "out": ROOT / "exp5/results/raw_zh"},
}


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def seg_names(picks_path: Path) -> list[str]:
    """picks.json 兩種形狀:exp1 是 {seg: {...}},exp5 是 [{seg: ...}, ...]。"""
    p = json.loads(picks_path.read_text())
    return list(p) if isinstance(p, dict) else [x["seg"] for x in p]


def keyterms_for(cfg, seg: str, cap: int = KEYTERM_CAP) -> list[str]:
    """exp1 的詞表是**逐地點**的,所以要看這一段屬於哪個 domain。"""
    if not cfg["dict"]:
        return []
    picks = json.loads(cfg["picks"].read_text())
    domain = picks[seg]["domain"]
    terms = json.loads(cfg["dict"].read_text())[domain]
    return [t["content"] for t in terms][:cap]


def transcribe(path: Path, lang: str, kt: list[str]) -> dict:
    # keyterms 必須是重複欄位(見檔頭坑 1)
    data = [("model_id", MODEL), ("language_code", lang)]
    data += [("keyterms", k) for k in kt]
    t0 = time.time()
    with open(path, "rb") as fh:
        r = requests.post(URL, headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
                          files={"file": (path.name, fh, "audio/wav")},
                          data=data, timeout=600)
    el = time.time() - t0
    r.raise_for_status()
    d = r.json()
    d["_elapsed_sec"] = round(el, 1)
    return d


def stems_for(cfg, corpus: str) -> list[str]:
    if corpus == "zh":
        man = json.loads((cfg["dir"] / "manifest.json").read_text())["windows"]
        return list(man)
    return [f"{s}__{c}" for s in seg_names(cfg["picks"]) for c in cfg["conds"]]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=list(CORPORA), required=True)
    ap.add_argument("--arm", default=None)
    ap.add_argument("--no-keyterms", action="store_true")
    ap.add_argument("--full-keyterms", action="store_true",
                    help="送完整詞表而非砍到 50 條(批次實測可吃 166 條)")
    ap.add_argument("--probe", action="store_true",
                    help="只跑一個檔並印出形狀,不寫結果")
    a = ap.parse_args()
    cfg = CORPORA[a.corpus]
    arm = a.arm or {"exp1": "Sbat_ja", "exp5": "Sbat_bi", "zh": "Zsc"}[a.corpus]
    stems = stems_for(cfg, a.corpus)

    if a.probe:
        stem = stems[0]
        seg = stem.split("__")[0] if "__" in stem else stem.split("_")[0]
        kt = [] if a.no_keyterms else keyterms_for(cfg, seg, 10**6 if a.full_keyterms else KEYTERM_CAP)
        d = transcribe(cfg["dir"] / f"{stem}.wav", cfg["lang"], kt)
        w = d.get("words", [])
        print(f"=== probe {stem}  keyterms={len(kt)}  {d['_elapsed_sec']}s")
        print(f"  language_code={d.get('language_code')} "
              f"prob={d.get('language_probability')} "
              f"audio={d.get('audio_duration_secs')}s")
        print(f"  words={len(w)}  第一個={ {k: v for k, v in w[0].items() if k in ('text','start','end','type')} if w else None}")
        print(f"  text: {d.get('text','')[:200]}")
        return

    raw = cfg["out"] / arm
    raw.mkdir(parents=True, exist_ok=True)
    todo = [s for s in stems if not (raw / f"{s}.json").exists()]
    if not todo:
        print("全部已完成")
        return

    meta = {"arm": arm, "engine": "ElevenLabs Scribe", "model_id": MODEL,
            "api": URL, "language_code": cfg["lang"],
            "keyterms": not a.no_keyterms,
            "keyterm_cap": "全部" if a.full_keyterms else KEYTERM_CAP,
            "keyterm_note": "exp1 逐地點詞表取前 50 條;無 sounds_like 讀音欄位",
            "diarize": False, "corpus": a.corpus,
            "rate_usd_per_hour": RATE_USD_PER_HOUR, "rate_checked": "2026-08-20"}
    (raw / "_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1))

    total_sec = 0.0
    print(f"送出 {len(todo)} 個檔(arm={arm}, keyterms={not a.no_keyterms})")
    for stem in todo:
        p = cfg["dir"] / f"{stem}.wav"
        seg = stem.split("__")[0] if "__" in stem else stem.split("_")[0]
        kt = [] if a.no_keyterms else keyterms_for(cfg, seg, 10**6 if a.full_keyterms else KEYTERM_CAP)
        d = transcribe(p, cfg["lang"], kt)
        dur = d.get("audio_duration_secs") or 0
        total_sec += dur
        (raw / f"{stem}.json").write_text(json.dumps({
            "arm": arm, "file": p.name, "audio_s": dur,
            "transcript": d.get("text", ""),
            "words": d.get("words", []),
            "meta": {**meta, "n_keyterms": len(kt),
                     "elapsed_sec": d["_elapsed_sec"],
                     "language_detected": d.get("language_code"),
                     "language_probability": d.get("language_probability"),
                     "transcription_id": d.get("transcription_id"),
                     "audio_sha256": sha256(p)},
        }, ensure_ascii=False, indent=1))
        print(f"  {stem}  {dur:.0f}s 音訊 / {d['_elapsed_sec']}s  "
              f"{len(d.get('text',''))}字 | {d.get('text','')[:52]}", flush=True)

    rate = RATE_USD_PER_HOUR["base"] + (0 if a.no_keyterms
                                        else RATE_USD_PER_HOUR["keyterms"])
    print(f"\n→ {raw}\n音訊合計 {total_sec/60:.1f} 分,"
          f"牌價估算 ${total_sec/3600*rate:.3f}(${rate}/hr,2026-08-20 查)")


if __name__ == "__main__":
    main()
