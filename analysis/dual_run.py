#!/usr/bin/env python3
"""「並排雙跑 + 人工複核」的可行性量化(handoff-v7 的正面產出)。

oracle 說「非即時多跑一個引擎有 +0.03~+0.13 的空間」,但那是**事後最佳選擇**。
真實流程是:兩個引擎都跑 → 標出分歧 → 人看分歧處。所以要先回答兩件事:

1. **複核負擔有多大?** 兩份稿子的分歧佔全文多少?這決定人力成本。
2. **人看了有沒有用?** 在分歧的專名上,兩邊各對多少次?
   若某一邊在分歧處幾乎總是對的,那就不必人看——直接用那一邊即可,
   **這個建議就不成立**。要接近五五開,複核才真的在做選擇。

純計算,不呼叫任何 API。
"""
import difflib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# exp1 的非即時兩隻:Gemini 批次 vs SM 批次 + 詞表
A, B = "Abat", "Cbplus"
CONDS = ["N0", "N1", "N2", "N3", "N4"]
TOKEN = re.compile(r"[0-9A-Za-z]+|[^\s0-9A-Za-z]")


def toks(s: str) -> list[str]:
    return TOKEN.findall(s or "")


def transcript(arm: str, seg: str, cond: str) -> str:
    f = ROOT / "results/raw" / arm / f"{seg}__{cond}.json"
    if not f.exists():
        return ""
    d = json.loads(f.read_text())
    return d.get("transcript") or d.get("input_transcription") or ""


def main() -> None:
    segs = list(json.loads((ROOT / "corpus/picks.json").read_text()))
    outcomes = json.loads((ROOT / "results/noun_outcomes.json").read_text())

    # ① 複核負擔:兩份稿子的 token 級分歧比例
    load = {}
    for cond in CONDS:
        tot = diff = 0
        for seg in segs:
            ta, tb = toks(transcript(A, seg, cond)), toks(transcript(B, seg, cond))
            if not ta or not tb:
                continue
            sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
            for op, i1, i2, j1, j2 in sm.get_opcodes():
                n = max(i2 - i1, j2 - j1)
                tot += n
                if op != "equal":
                    diff += n
        load[cond] = round(diff / tot, 4) if tot else None

    # ② 分歧處誰對:只看兩邊判定不同的專名
    idx = {}
    for r in outcomes:
        if r["arm"] in (A, B):
            idx.setdefault((r["cond"], r["seg"], r["noun"]), {})[r["arm"]] = r["tolerant"]
    split = {}
    for cond in CONDS:
        a_only = b_only = agree_hit = agree_miss = 0
        for (c, _s, _n), per in idx.items():
            if c != cond or A not in per or B not in per:
                continue
            ha, hb = per[A], per[B]
            if ha and hb:
                agree_hit += 1
            elif not ha and not hb:
                agree_miss += 1
            elif ha:
                a_only += 1
            else:
                b_only += 1
        n_split = a_only + b_only
        split[cond] = {
            "只有 Gemini 批次對": a_only, "只有 SM 批次+詞表對": b_only,
            "兩邊都對": agree_hit, "兩邊都錯": agree_miss,
            "分歧數": n_split,
            "分歧中 Gemini 對的比例": round(a_only / n_split, 3) if n_split else None,
        }

    out = {"note": "並排雙跑的複核負擔與分歧處勝負。純計算,無 API 呼叫。",
           "arms": [A, B],
           "text_divergence_ratio": load,
           "proper_noun_split": split}
    (ROOT / "results/dual_run.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))

    print(f"並排雙跑:{A} vs {B}\n")
    print("cond  逐字稿分歧比例  專名分歧數  只有Gemini對  只有SM對  Gemini佔比")
    for c in CONDS:
        s = split[c]
        ratio = s["分歧中 Gemini 對的比例"]
        print(f"{c}    {load[c]:>8.3f}      {s['分歧數']:>8}  {s['只有 Gemini 批次對']:>12}"
              f"  {s['只有 SM 批次+詞表對']:>8}  "
              f"{'—' if ratio is None else format(ratio, '.2f'):>9}")
    print("\n→ results/dual_run.json")


if __name__ == "__main__":
    main()
