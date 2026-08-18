#!/usr/bin/env python3
"""exp5 arm Xbat_bi:Speechmatics **batch** API,language=cmn_en,無詞表。

## 為什麼是 batch 不是 realtime(明確偏離,不可與 exp2 的 X_bi 混為一談)

exp2 的 X_bi 是 realtime WS + 1× 推流。exp5 原本也要照跑,但這個環境的
SM realtime 併發上限是 1,而且前面幾次中斷留下的 session 沒有即時釋放,
造成後續連線無回應地掛住(第一個檔正常、之後全部卡死,重試也沒有錯誤訊息)。

改用 batch 的理由不只是「跑得動」:
  - exp5 要回答的是**術語召回**,不是延遲。batch 與 realtime 的差別在後者
    有增量改寫與 max_delay 限制,對「這個詞有沒有被聽出來」影響有限。
  - **對照組 Breeze 本來就是離線 sequential 解碼**。用 batch 對 Breeze,
    兩邊都是「拿到完整音檔慢慢想」,反而比 realtime 對 offline 更公平。
  - exp1 §5 已經做過 batch 當對照的先例。

arm 名稱刻意叫 `Xbat_bi` 而不是 `X_bi`,避免日後有人把它跟 exp2 的
realtime 數字放進同一欄比較。
"""
import json, os, sys, time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
COND = ROOT / "corpus" / "conditions"
OUT = ROOT / "results" / "raw" / "Xbat_bi"
S_KEY = os.environ["s_key"]
BASE = "https://asr.api.speechmatics.com/v2"


def run_one(wav: Path) -> dict:
    conf = {"type": "transcription",
            "transcription_config": {"language": "cmn_en", "operating_point": "enhanced"}}
    t0 = time.time()
    with open(wav, "rb") as f:
        r = requests.post(f"{BASE}/jobs", headers={"Authorization": f"Bearer {S_KEY}"},
                          files={"data_file": (wav.name, f, "audio/wav"),
                                 "config": (None, json.dumps(conf))}, timeout=180)
    r.raise_for_status()
    job = r.json()["id"]
    for _ in range(180):
        time.sleep(5)
        # 查不到 job(例如被人手動刪掉)不要用 KeyError 炸掉,講清楚發生什麼事
        body = requests.get(f"{BASE}/jobs/{job}",
                            headers={"Authorization": f"Bearer {S_KEY}"}, timeout=60).json()
        if "job" not in body:
            raise RuntimeError(f"job {job} 查不到(可能已被刪除):{str(body)[:120]}")
        st = body["job"]["status"]
        if st == "done":
            break
        if st in ("rejected", "expired"):
            raise RuntimeError(f"SM {st}")
    else:
        raise RuntimeError("逾時")
    txt = requests.get(f"{BASE}/jobs/{job}/transcript?format=txt",
                       headers={"Authorization": f"Bearer {S_KEY}"}, timeout=120)
    # mojibake 防呆:r.text 會用錯編碼,一定要自己 decode(exp1 踩過)
    return {"arm": "Xbat_bi", "file": wav.name, "job": job,
            "audio_s": 300.0, "transcript": txt.content.decode("utf-8").strip(),
            "elapsed_sec": round(time.time() - t0, 1)}


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[1:]) or None
    for wav in sorted(COND.glob("*.wav")):
        if only and wav.stem not in only:
            continue
        dst = OUT / f"{wav.stem}.json"
        if dst.exists():
            print(f"  跳過(已有){wav.stem}", flush=True)
            continue
        d = run_one(wav)
        dst.write_text(json.dumps(d, ensure_ascii=False, indent=1))
        print(f"  {wav.stem}  {d['elapsed_sec']}s  {len(d['transcript'])} 字", flush=True)
