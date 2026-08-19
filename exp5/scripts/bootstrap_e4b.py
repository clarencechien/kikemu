#!/usr/bin/env python3
"""handoff-v9 K:E4B 的 segment-cluster bootstrap CI 與配對比較。

**純計算,不呼叫任何 API、不碰音檔、不花錢。**
輸入是既有的 `exp5/results/term_outcomes.json`(per-item outcome)。

**為什麼要補這格:** Breeze 與 SM 的比較都有 CI(報告 §3E.3),E4B 沒有——
現在講「E4B 與 Speechmatics batch 同級」是拿兩個點估計在比,沒有不確定性。

方法與 `scripts/aggregate.py::bootstrap_delta` 相同(重採樣的單位是
segment 而不是 term,因為同一段裡的術語不獨立),只有 seed 換成 20260819
以免與 exp1 那批混淆。

⚠️ **一個容易踩到的地方**:`term_outcomes.json` 的每一列是一個**術語型**
(`tolerant` 是 0/1),但 `n` 欄是它在該段出現的**實例數**。報告 §3E.2 / §3E.3
的召回一律是**實例加權**(micro),所以這裡也必須乘 `n` 再加總。
初版沒乘,`E4B − SM batch` 算出 −0.006(型平均);乘了之後是 +0.029(實例加權)
——**連正負號都不一樣**,而 §3E.3 對得上的是後者。

**只有 3 個 segment cluster,CI 必然很寬——這是 n=3 的實話,不是精度。**

主數字用**貪婪**那組(`Xgma_e4b_greedy`),因為 handoff-v9 J 量到 E4B 的
取樣會讓數字掉 0.04~0.05,而貪婪是可重現的。取樣那組(`Xgma_e4b`)一併算,
兩組並列。

用法:
    python3 exp5/scripts/bootstrap_e4b.py
"""
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOOT = 5000
SEED = 20260819
KEY = "tolerant"

# (a, b) → 算 recall(a) − recall(b)
PAIRS = [
    ("Xgma_e4b_greedy", "Xbat_bi"),      # E4B 對 SM batch:「同級」到底是不是
    ("Xgma_e4b_greedy", "Xbrz_auto"),    # E4B 對 Breeze:Breeze 的優勢站不站得住
    ("Xgma_e4b_greedy", "Xgma_e4b"),     # 貪婪 對 取樣:J 那格的差有沒有意義
    ("Xgma_e4b", "Xbat_bi"),             # 取樣版對 SM batch(對照用)
]
LABEL = {"Xgma_e4b_greedy": "E4B(貪婪)", "Xgma_e4b": "E4B(取樣)",
         "Xbat_bi": "SM batch", "Xbrz_auto": "Breeze"}


def bootstrap_delta(outcomes, a, b, conds):
    """Segment-cluster bootstrap:重採樣單位是 segment,不是 term。"""
    # 存 (命中數, 實例數):實例加權才與報告 §3E.2 / §3E.3 同一把尺
    by_seg = defaultdict(lambda: defaultdict(list))
    for o in outcomes:
        if o["cond"] in conds and o["arm"] in (a, b):
            by_seg[o["seg"]][o["arm"]].append((o[KEY] * o["n"], o["n"]))
    segs = sorted(by_seg)
    if not segs:
        return None
    # 兩個 arm 都要有資料,否則這個比較不成立
    if not all(by_seg[s].get(a) and by_seg[s].get(b) for s in segs):
        return None
    rng = random.Random(SEED)
    deltas = []
    for _ in range(BOOT):
        pick = [segs[rng.randrange(len(segs))] for _ in segs]
        na = sum(h for s in pick for h, _ in by_seg[s][a])
        da = sum(n for s in pick for _, n in by_seg[s][a])
        nb = sum(h for s in pick for h, _ in by_seg[s][b])
        db = sum(n for s in pick for _, n in by_seg[s][b])
        if da and db:
            deltas.append(na / da - nb / db)
    deltas.sort()
    pa = (sum(h for s in segs for h, _ in by_seg[s][a])
          / sum(n for s in segs for _, n in by_seg[s][a]))
    pb = (sum(h for s in segs for h, _ in by_seg[s][b])
          / sum(n for s in segs for _, n in by_seg[s][b]))
    return {"delta": round(pa - pb, 4), "n_seg": len(segs),
            "ci95": [round(deltas[int(len(deltas) * 0.025)], 4),
                     round(deltas[int(len(deltas) * 0.975)], 4)]}


def main() -> None:
    outcomes = json.loads((ROOT / "results" / "term_outcomes.json").read_text())
    out = {"_": "handoff-v9 K:E4B 的 segment-cluster bootstrap",
           "boot": BOOT, "seed": SEED, "key": KEY,
           "weighting": "實例加權(乘 term_outcomes 的 n 欄),與報告 §3E.2/§3E.3 一致",
           "note": "重採樣單位是 segment(只有 3 個),CI 必然寬", "pairs": {}}
    rows = []
    for a, b in PAIRS:
        for tag, conds in (("全部", {"M0", "M3"}), ("僅 M0", {"M0"}), ("僅 M3", {"M3"})):
            r = bootstrap_delta(outcomes, a, b, conds)
            if r is None:
                continue
            name = f"{a}-{b}-{tag}"
            out["pairs"][name] = r
            excl = "不含 0" if (r["ci95"][0] > 0) == (r["ci95"][1] > 0) else "**含 0**"
            rows.append((f"{LABEL[a]} − {LABEL[b]}", tag, r["delta"], r["ci95"], excl))

    (ROOT / "results" / "e4b_bootstrap.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))

    print(f"exp5 E4B 配對比較(segment-cluster bootstrap {BOOT} 次,seed {SEED})")
    print(f"{'比較':<26}{'條件':<8}{'Δ':>9}{'95% CI':>22}  ")
    print("-" * 72)
    last = None
    for label, tag, d, ci, excl in rows:
        if last and label != last:
            print()
        print(f"{label:<26}{tag:<8}{d:>+9.3f}   [{ci[0]:+.3f}, {ci[1]:+.3f}]  {excl}")
        last = label
    print("\n→ exp5/results/e4b_bootstrap.json")
    print("⚠️ 只有 3 個 segment cluster,CI 必然寬。這足以判斷方向,"
          "不足以精確估計差距大小。")


if __name__ == "__main__":
    main()
