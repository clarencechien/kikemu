#!/usr/bin/env python3
"""exp5 噪音響應曲線:四個聲學條件下各 arm 的 tolerant 術語召回。

`compare.py` 只印 M0/M3 兩端(其他 arm 只跑了那兩格)。handoff-v8 B 把
Breeze 補到 M0–M3 四條件全跑,所以「掉幅」不再只是兩點連線——
這支就是把那條曲線印出來,順便對照 CPU/fp32 與 T4/fp16 的差異。

判讀規則寫在 handoff-v8 §4 B,先寫死:中間塌陷 → 有門檻效應;
dtype 差 < 0.02 → exp5 侷限 17 可解除。

用法:
    python3 exp5/scripts/noise_curve.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONDS = ["M0", "M1", "M2", "M3"]
SEGS = ["T1", "T2", "T3"]
ARMS = [("Xbrz_gpu", "Breeze T4/fp16"), ("Xbrz_auto", "Breeze CPU/fp32"),
        ("Xbat_bi", "SM batch"), ("Gbat37", "Gemini 3.7 批次"),
        ("Gbat", "Gemini 3.5 批次"),
        ("G", "Gemini Live"), ("Xgma_12b", "Gemma 4 12B"), ("Xgma_e4b", "Gemma 4 E4B")]


def main() -> None:
    idx = {(r["arm"], r["seg"], r["cond"]): r
           for r in json.loads((ROOT / "results/scores.json").read_text())}

    def cell(arm: str, cond: str):
        """條件平均。實例數逐段不同,所以用實例加權而非段落平均。"""
        hit = tot = 0
        for s in SEGS:
            r = idx.get((arm, s, cond))
            if r is None:
                return None
            hit += r["recall_tolerant"] * r["n_inst"]
            tot += r["n_inst"]
        return hit / tot if tot else None

    def fmt(v):
        return "   —   " if v is None else f"{v:.3f}  "

    n = sum(idx[("Xbrz_gpu", s, "M0")]["n_inst"] for s in SEGS)
    print(f"exp5 噪音曲線(tolerant 術語召回,{n} 實例/條件)\n")
    head = "arm".ljust(24) + "".join(c.rjust(9) for c in CONDS) + "     掉幅"
    print(head)
    print("-" * len(head))
    rows = {}
    for arm, label in ARMS:
        v = [cell(arm, c) for c in CONDS]
        rows[arm] = v
        drop = (f"{v[0] - v[3]:+.3f}" if v[0] is not None and v[3] is not None
                else "   —  ")
        print(label.ljust(24) + "".join(fmt(x).rjust(9) for x in v) + "   " + drop)

    gpu, cpu = rows["Xbrz_gpu"], rows["Xbrz_auto"]
    d = [(c, g - p) for c, g, p in zip(CONDS, gpu, cpu) if g is not None and p is not None]
    print("\ndtype(T4/fp16 − CPU/fp32):" +
          "  ".join(f"{c} {v:+.3f}" for c, v in d))
    worst = max(abs(v) for _c, v in d)
    print(f"  最大絕對差 {worst:.3f} → " +
          ("< 0.02,exp5 侷限 17 可解除" if worst < 0.02 else "≥ 0.02,數值受 dtype 影響"))

    # 中間兩格與 M0–M3 連線的偏離:正=比連線高,負=塌陷
    line = [gpu[0] + (gpu[3] - gpu[0]) * i / 3 for i in range(4)]
    print("\nBreeze M1/M2 對 M0–M3 連線的偏離:" +
          "  ".join(f"{CONDS[i]} {gpu[i] - line[i]:+.3f}" for i in (1, 2)))


if __name__ == "__main__":
    main()
