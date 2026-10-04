"""定稿節奏:多久來一則定稿、開場多久才有第一則。純計算,不呼叫任何 API。

為什麼要有:使用者回報 Gemini 對照模式「超過 10 秒也沒有吐句子」。產品裡譯文是
**定稿**觸發的(暫定只顯示灰字),所以定稿間隔就是「譯文最久會空白多久」的下限。
這支把 handoff-v12 的 GTv(gemini-3.5-transcribe-live + 詞表)與 exp1 的 C+
(SM enhanced + 詞表)放在一起比,兩者都是 1× 即時推流、同一批 30 檔。

輸入:results/raw/GTv/*.json(log kind = inputTranscription)
      results/raw/Cplus/*.json(log kind = AddTranscript)
      兩者 log 的 t 都是牆鐘秒;起點 = setupComplete / StartRecognition_sent。
      空字串的定稿不算(SM 偶爾回空的 AddTranscript)。

用法:python3 analysis/final_cadence.py → results/final_cadence.json
"""

from __future__ import annotations

import collections
import glob
import json
import os
import statistics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "results", "raw")
OUT = os.path.join(ROOT, "results", "final_cadence.json")

ARMS = {
    "GTv": {"kind": "inputTranscription", "label": "gemini-3.5-transcribe-live + 詞表(handoff-v12)"},
    "Cplus": {"kind": "AddTranscript", "label": "Speechmatics enhanced + 詞表(exp1 C+,產品導覽模式的耳朵)"},
}
START = ("setupComplete", "StartRecognition_sent")


def q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(p * len(xs)))], 1)


def main():
    res: dict = {"source": "results/raw/{GTv,Cplus},30 檔 = 6 段 × N0–N4,1× 即時推流", "arms": {}}
    for arm, cfg in ARMS.items():
        gaps: dict[str, list[float]] = collections.defaultdict(list)
        first: dict[str, list[float]] = collections.defaultdict(list)
        for f in sorted(glob.glob(os.path.join(RAW, arm, "*__N*.json"))):
            d = json.load(open(f, encoding="utf-8"))
            cond = os.path.basename(f)[:-5].split("__")[1]
            start = next(e["t"] for e in d["log"] if e["kind"] in START)
            ts = [e["t"] for e in d["log"] if e["kind"] == cfg["kind"] and (e.get("text") or e.get("transcript"))]
            pts = [start] + ts
            gaps[cond] += [b - a for a, b in zip(pts, pts[1:])]
            # 整檔都沒有定稿:記成整段音訊長度(下限,實際是「從來沒來」)
            first[cond].append(ts[0] - start if ts else d["audio_s"])
        res["arms"][arm] = {
            "label": cfg["label"],
            "by_condition": {
                c: {
                    "finals": len(gaps[c]),
                    "gap_p50_s": q(gaps[c], 0.5),
                    "gap_p90_s": q(gaps[c], 0.9),
                    "gap_max_s": round(max(gaps[c]), 1),
                    "first_final_median_s": round(statistics.median(first[c]), 1),
                    "first_final_max_s": round(max(first[c]), 1),
                }
                for c in sorted(gaps)
            },
        }
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    for arm, a in res["arms"].items():
        print(arm, a["label"])
        for c, m in a["by_condition"].items():
            print(
                f"  {c} 定稿間隔 p50 {m['gap_p50_s']}s p90 {m['gap_p90_s']}s max {m['gap_max_s']}s"
                f"  首個定稿 中位 {m['first_final_median_s']}s max {m['first_final_max_s']}s"
            )


if __name__ == "__main__":
    main()
