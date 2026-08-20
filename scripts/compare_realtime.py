#!/usr/bin/env python3
"""handoff-v10 S4:即時三方對照(Scribe / SM 50 詞 / SM 完整詞表)。

**純計算,不呼叫 API。** 讀 `results/scores.json` 與各 arm 的 log。

判讀規則寫在 handoff-v10 §4(**先寫死**),這支只負責把數字擺出來:

    A = Srt_ja    Scribe v2 Realtime + 50 keyterms
    B = Cplus50   SM 即時 + 同樣 50 詞、拿掉 sounds_like  ← 詞表對齊控制組
    C = Cplus     SM 即時 + 完整詞表 142~166 詞 + 假名(現行產線)

**A − B 才是引擎的差**(詞表已對齊);A − C 混著引擎與詞表兩個變因,
不可單獨引用來講「誰比較差」。

延遲那一欄要小心:**Scribe 的 partial 沒有時間戳**,所以暫定延遲與 SM
不可直接比(§4c)。這裡只印可比的那兩項:**定稿 p50** 與 **改寫率**。

用法:
    python3 scripts/compare_realtime.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from score import sm_latency, sm_rewrite_rate  # noqa: E402

CONDS = ["N0", "N1", "N2", "N3", "N4"]
ARMS = [("Srt_ja", "A  Scribe 即時 + 50 詞"),
        ("Cplus50", "B  SM 即時 + 50 詞(控制組)"),
        ("Cplus", "C  SM 即時 + 完整詞表+假名"),
        ("C", "   SM 即時無詞表"),
        ("A", "   Gemini Live")]


def micro(rows, arm, cond=None):
    h = t = 0
    for r in rows:
        if r["arm"] != arm:
            continue
        if cond and r["cond"] != cond:
            continue
        h += r["recall_tolerant"] * r["n_nouns"]
        t += r["n_nouns"]
    return h / t if t else None


def logs_for(arm):
    d = ROOT / "results" / "raw" / arm
    out = []
    for f in sorted(d.glob("*.json")):
        if f.name == "_meta.json":
            continue
        j = json.loads(f.read_text())
        if j.get("log"):
            out.append(j["log"])
    return out


def main() -> None:
    rows = json.loads((ROOT / "results" / "scores.json").read_text())
    print("handoff-v10 S4:即時三方對照(exp1 日文,67 專名 × 5 條件)\n")
    head = f"{'arm':<30}{'全部':>8}" + "".join(c.rjust(8) for c in CONDS)
    print(head)
    print("-" * len(head))
    have = {}
    for arm, label in ARMS:
        v = micro(rows, arm)
        if v is None:
            continue
        have[arm] = v
        print(f"{label:<30}{v:>8.3f}" +
              "".join(f"{micro(rows, arm, c):>8.3f}" for c in CONDS))

    if "Srt_ja" in have and "Cplus50" in have:
        print(f"\n  A − B(引擎的差,詞表已對齊)= {have['Srt_ja'] - have['Cplus50']:+.3f}")
    if "Cplus" in have and "Cplus50" in have:
        print(f"  C − B(完整詞表 + 假名的價值)= {have['Cplus'] - have['Cplus50']:+.3f}")
    if "Srt_ja" in have and "Cplus" in have:
        print(f"  A − C(現實的差,含詞表劣勢)= {have['Srt_ja'] - have['Cplus']:+.3f}"
              "   ← 混變因,不可單獨引用")

    print("\n可比的延遲與改寫(Scribe 的 partial 無時間戳,暫定延遲不可比,§4c)\n")
    print(f"{'arm':<30}{'定稿 p50':>10}{'定稿 p90':>10}{'改寫率':>9}{'n 檔':>6}")
    for arm, label in ARMS:
        ls = logs_for(arm)
        if not ls:
            continue
        fin = [sm_latency(l).get("final_p50") for l in ls]
        f90 = [sm_latency(l).get("final_p90") for l in ls]
        rw = [sm_rewrite_rate(l) for l in ls]
        fin = sorted(x for x in fin if x is not None)
        f90 = sorted(x for x in f90 if x is not None)
        rw = [x for x in rw if x is not None]
        if not fin:
            continue
        print(f"{label:<30}{fin[len(fin)//2]:>10.2f}{f90[len(f90)//2]:>10.2f}"
              f"{sum(rw)/len(rw):>9.3f}{len(ls):>6}")

    # 空輸出:N3 是唯一能觸發「整段放棄」的條件
    print("\nN3(人群 8dB)空/極短輸出")
    for arm, label in ARMS:
        d = ROOT / "results" / "raw" / arm
        fs = sorted(d.glob("*__N3.json")) if d.exists() else []
        if not fs:
            continue
        n = sum(1 for f in fs
                if len((json.loads(f.read_text()).get("transcript") or "").strip()) < 20)
        print(f"  {label:<30}{n}/{len(fs)}")


if __name__ == "__main__":
    main()
