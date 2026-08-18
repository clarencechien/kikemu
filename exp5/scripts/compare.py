#!/usr/bin/env python3
"""exp5 三方比較:Gemini / Speechmatics / Breeze。

依 handoff-v6 §5 的**預先寫死**判讀規則輸出結論,不看到數字才決定怎麼解釋。
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARMS = [("G", "Gemini Live"), ("Xbat_bi", "Speechmatics batch"),
        ("X_bi", "Speechmatics realtime"), ("Xbrz_auto", "Breeze ASR 25")]
CELLS = [(s, c) for s in ("T1", "T2", "T3") for c in ("M0", "M3")]


def load():
    idx = {}
    for r in json.loads((ROOT / "results/scores.json").read_text()):
        idx[(r["arm"], r["seg"], r["cond"])] = r
    return idx


def fmt(v):
    return "  —  " if v is None else f"{v:.3f}"


def main():
    idx = load()
    print("tolerant 術語召回(—=該格未跑)\n")
    head = "arm".ljust(22) + "".join(f"{s}-{c}".rjust(8) for s, c in CELLS) + "    平均"
    print(head)
    print("-" * len(head))
    avg = {}
    for arm, label in ARMS:
        vals = [idx.get((arm, s, c), {}).get("recall_tolerant") for s, c in CELLS]
        got = [v for v in vals if v is not None]
        avg[arm] = sum(got) / len(got) if got else None
        print(label.ljust(22) + "".join(fmt(v).rjust(8) for v in vals) +
              "  " + fmt(avg[arm]) + f"  ({len(got)}/6)")

    # 只在兩個 arm 都有值的格子上比,避免拿不同子集的平均互減
    def delta(a, b, conds=None):
        pairs = [(idx.get((a, s, c), {}).get("recall_tolerant"),
                  idx.get((b, s, c), {}).get("recall_tolerant"))
                 for s, c in CELLS if conds is None or c in conds]
        pairs = [(x, y) for x, y in pairs if x is not None and y is not None]
        if not pairs:
            return None, 0
        return sum(x - y for x, y in pairs) / len(pairs), len(pairs)

    print("\n配對比較(只算兩邊都有結果的格子)\n")
    for a, b in (("Xbrz_auto", "Xbat_bi"), ("Xbrz_auto", "G"), ("G", "Xbat_bi")):
        for tag, conds in (("全部", None), ("僅 M0", {"M0"}), ("僅 M3", {"M3"})):
            dv, n = delta(a, b)if conds is None else delta(a, b, conds)
            print(f"  {a} − {b}  {tag:<6} Δ={fmt(dv)}  n={n}")
        print()

    d_all, n_all = delta("Xbrz_auto", "Xbat_bi")
    d_m3, n_m3 = delta("Xbrz_auto", "Xbat_bi", {"M3"})
    if d_all is None:
        print("Breeze 尚未有可配對的結果,判讀規則暫不適用。")
        return
    if d_all >= 0.15 and (d_m3 or 0) > 0:
        v = "≥ +0.15 且 M3 仍領先 → 優勢可推廣,值得跑 Phase B 與地端評估"
    elif d_all >= 0.05:
        v = "+0.05 ~ +0.15 → 有優勢但被污染放大;領域內強、領域外小贏,不改架構"
    else:
        v = "≈ 0 或為負 → Phase A 的領先主要來自訓練污染,X-breeze 結案"
    print(f"handoff-v6 §5 判讀:Δ(全部)={d_all:+.3f} n={n_all}、"
          f"Δ(M3)={fmt(d_m3)} n={n_m3}\n  → {v}")
    print("\n注意:規則寫的基準是 X_bi(realtime),但 exp5 的 SM 主要跑 batch。"
          "T1__M0 上 realtime 比 batch 高 0.057,所以用 batch 當基準是**高估** Breeze 的 Δ。")


if __name__ == "__main__":
    main()
