#!/usr/bin/env python3
"""handoff-v7 §2 錯誤互補性:Jaccard + 可部署子集的 oracle。

與 `oracle.py` 分開的理由:oracle.py 算「全 arm 天花板」,
這支回答決策真正要問的「**在同一個產品情境下能同時跑的那幾個 arm**,
合起來有沒有空間」。exp1 的批次 arm 在即時場景根本拿不到,
把它們算進去會給出一個買不到的天花板。

純計算,不呼叫任何 API。
"""
import itertools
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import EXPERIMENTS, load, recall, covered_arms  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# 「可部署子集」= 同一個產品情境下真的能同時跑的 arm。
SUBSETS = [
    ("exp1", "即時可部署", ["A", "C", "Cplus"],
     "現場字幕:Gemini Live + SM 即時(±詞表)。批次 arm 拿不到。"),
    ("exp1", "非即時可部署", ["Abat", "Cb", "Cbplus"],
     "事後逐字稿:Gemini 批次 + SM 批次(±詞表)。"),
    ("exp1", "跨引擎最小組", ["A", "Cplus"],
     "一個 LLM + 一個專用 ASR,兩家不同引擎。"),
    ("exp1", "同引擎不同設定", ["C", "Cb"],
     "對照組:同一家引擎的即時 vs 批次。若這組也有增益,代表增益來自抖動不是互補。"),
    ("exp5", "地端 + 雲端", ["Xbrz_auto", "Xbat_bi"],
     "唯一乾淨的跨家族測試:Breeze(地端)+ SM(雲端),都不與參考同源。"),
]


def pair_jaccard(table, a, b, cond, judge="tolerant"):
    j = 1 if judge == "strict" else 2
    both = either = 0
    for (c, _s, _i), per in table.items():
        if c != cond or a not in per or b not in per:
            continue
        ea, eb = not per[a][j], not per[b][j]
        either += ea or eb
        both += ea and eb
    return (both / either) if either else None


def main() -> None:
    out = {"note": "handoff-v7 §2:可部署子集的 oracle 與錯誤互補性。純計算。",
           "subsets": []}
    for exp, name, arms, why in SUBSETS:
        spec = EXPERIMENTS[exp]
        table = load(spec)
        entry = {"experiment": exp, "subset": name, "arms": arms,
                 "why": why, "conds": {}}
        for cond in spec["conds"]:
            av = [a for a in arms if a in covered_arms(table, arms, cond)]
            if len(av) < 2:
                continue
            cell = {"arms_present": av}
            for judge in ("strict", "tolerant"):
                singles = {a: recall(table, [a], cond, judge)[0] for a in av}
                best = max(singles.values())
                orc, n = recall(table, av, cond, judge)
                cell[judge] = {"single": {a: round(v, 4) for a, v in singles.items()},
                               "best_single": round(best, 4),
                               "oracle": round(orc, 4),
                               "gain": round(orc - best, 4), "n_items": n}
            cell["jaccard_tolerant"] = {
                f"{a}+{b}": (round(v, 4) if (v := pair_jaccard(table, a, b, cond)) is not None else None)
                for a, b in itertools.combinations(av, 2)}
            entry["conds"][cond] = cell
        out["subsets"].append(entry)

    (ROOT / "results/complementarity.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))

    for e in out["subsets"]:
        print(f"\n{e['experiment']} — {e['subset']}  ({'+'.join(e['arms'])})")
        for cond, c in e["conds"].items():
            t = c["tolerant"]
            js = c["jaccard_tolerant"]
            jtxt = " ".join(f"{k}:J={v}" for k, v in js.items())
            print(f"  {cond}  best={t['best_single']:.3f}  oracle={t['oracle']:.3f}"
                  f"  gain=+{t['gain']:.3f}   {jtxt}")
    print("\n→ results/complementarity.json")


if __name__ == "__main__":
    main()
