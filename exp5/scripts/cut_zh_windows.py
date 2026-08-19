#!/usr/bin/env python3
"""從 exp5 的音檔切出**純中文**的 20 秒視窗,並對齊出每個視窗的參考文本。

**為什麼要這一組:** Gemma 4 12B 在 exp5 只有 0.056,在 exp1(純日文、不切塊)
是 0.323——中間還有一個變因沒拆開:**台灣華語本身難不難**。
exp5 的語料是中英夾雜,沒有純中文段落可用;但夾雜是**局部**的,
整段音訊裡有大片沒講到英文的地方,把那些切出來就是天然的純中文語料。

規則**先寫死**,不看到結果才調:

    · 視窗長度 20 秒(< 模型卡 30 秒上限 → 單發送出,零切塊 handicap)
    · 起點每 5 秒掃一次,取**完全不含拉丁字母**的視窗
    · 重疊的取最早那個(貪婪不重疊)
    · 音源用 M0(乾淨),噪音變因這一組不測
    · **SM 的轉寫與對齊出來的參考文本,兩邊都不能含拉丁字母**
      (初版只檢查 SM 的輸出,漏掉 SM 把 Mario / DevCore 聽成中文的視窗;
       規則在**跑任何模型之前**補嚴,不是看到結果才調)

定位靠 Speechmatics 批次的**詞級時間戳**(job id 存在既有結果裡,重抓免費)。
參考文本用 SequenceMatcher 把 SM 的詞流對齊到 exp5 的參考,再依視窗切片——
與 `build_reference.py::sm_align()` 同一個做法。

輸出:
    exp5/corpus/zh_windows/<seg>_<start>s.wav      20 秒切片
    exp5/corpus/zh_windows/manifest.json           視窗清單 + sha256 + 參考文本
"""
import difflib
import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
COND = ROOT / "corpus" / "conditions"
REF = ROOT / "corpus" / "reference"
OUT = ROOT / "corpus" / "zh_windows"
SMW = ROOT / "corpus" / "sm_words"
SEGS = ["T1", "T2", "T3"]
WIN = 20            # 秒
STEP = 5            # 掃描步進
SR = 16000
LAT = re.compile(r"[A-Za-z]")
TOKEN = re.compile(r"[0-9A-Za-z]+|[^\s0-9A-Za-z]")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def sm_words(seg: str):
    """SM 詞級結果。job id 在既有的 Xbat_bi 結果裡,重抓不用錢。"""
    SMW.mkdir(parents=True, exist_ok=True)
    p = SMW / f"{seg}.json"
    if not p.exists():
        job = json.loads((ROOT / "results/raw/Xbat_bi" / f"{seg}__M0.json").read_text())["job"]
        r = requests.get(
            f"https://asr.api.speechmatics.com/v2/jobs/{job}/transcript?format=json-v2",
            headers={"Authorization": f"Bearer {os.environ['s_key']}"}, timeout=60)
        r.raise_for_status()
        p.write_text(json.dumps(r.json(), ensure_ascii=False))
    j = json.loads(p.read_text())
    return [(x["start_time"], x["end_time"], x["alternatives"][0]["content"])
            for x in j["results"] if x["type"] == "word"]


def pick_windows(words):
    """完全不含拉丁字母的 20 秒視窗,貪婪不重疊。"""
    if not words:
        return []
    end = int(words[-1][1])
    picked = []
    for st in range(0, max(end - WIN, 0) + 1, STEP):
        en = st + WIN
        inside = [c for s, e, c in words if s >= st and e <= en]
        if not inside or any(LAT.search(c) for c in inside):
            continue
        if picked and st < picked[-1][1]:
            continue
        picked.append((st, en, "".join(inside)))
    return picked


def ref_slice(seg: str, words, st: float, en: float) -> str:
    """把 SM 的詞流對齊到參考文本,取出這個視窗對應的參考片段。

    SM 與參考是兩個獨立來源,所以只能用序列比對找對應位置;
    對不上的視窗會回傳空字串,由呼叫端剔除。
    """
    ref_toks = TOKEN.findall((REF / f"{seg}.txt").read_text())
    sm_toks = [c for _s, _e, c in words]
    sm = difflib.SequenceMatcher(None, sm_toks, ref_toks, autojunk=False)
    # SM token index → 參考 token index
    amap: dict[int, int] = {}
    for i, j, n in sm.get_matching_blocks():
        for k in range(n):
            amap[i + k] = j + k
    idx = [i for i, (s, e, _c) in enumerate(words) if s >= st and e <= en]
    hits = [amap[i] for i in idx if i in amap]
    if len(hits) < 5:
        return ""
    return "".join(ref_toks[min(hits):max(hits) + 1])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    man = {"_": f"純中文 {WIN} 秒視窗;規則見 cut_zh_windows.py 檔頭,先寫死不事後調",
           "window_sec": WIN, "scan_step_sec": STEP, "source_cond": "M0",
           "windows": {}}
    for seg in SEGS:
        words = sm_words(seg)
        audio, sr = sf.read(COND / f"{seg}__M0.wav", dtype="float32")
        assert sr == SR
        for st, en, sm_text in pick_windows(words):
            ref = ref_slice(seg, words, st, en)
            if not ref:
                print(f"  跳過 {seg} {st}-{en}s:對不上參考文本")
                continue
            if LAT.search(ref):
                eng = "".join(sorted(set(LAT.findall(ref))))
                print(f"  跳過 {seg} {st}-{en}s:參考裡有拉丁字母"
                      f"({[w for w in re.findall(r'[A-Za-z]+', ref)]})")
                continue
            stem = f"{seg}_{st}s"
            p = OUT / f"{stem}.wav"
            sf.write(p, audio[st * SR:en * SR], SR, subtype="PCM_16")
            man["windows"][stem] = {
                "seg": seg, "start_sec": st, "end_sec": en,
                "sha256": sha256(p), "reference": ref, "sm_text": sm_text}
            print(f"  {stem}  參考 {len(ref)} 字 | {ref[:50]}")
    (OUT / "manifest.json").write_text(json.dumps(man, ensure_ascii=False, indent=1))
    print(f"\n共 {len(man['windows'])} 個視窗 → {OUT}")


if __name__ == "__main__":
    main()
