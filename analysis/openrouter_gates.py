"""handoff-v16 §8.6–§8.8:Gate A(Grok 能否取代 Scribe)、Gate B(便宜 / 可本機化模型)、經 OpenRouter 與直連的一致性。

**純計算,不呼叫任何 API。** 輸入是跑完 score 之後的 per-item outcome 檔:
  exp5 → exp5/results/term_outcomes.json(實例加權,n = 術語實例數)
  exp1 → results/noun_outcomes.json(每個專名等權)
分歧數 / 勝率 / Jaccard 的定義與 `analysis/complementarity_pairs.py`(handoff-v11)相同:
  分歧數 = 恰好一邊對的項目(exp5 以實例數加權)
  專用 ASR 勝率 = 只有專用 ASR 對 /(只有專用 ASR 對 + 只有 Gemini 對)
  Jaccard = 兩邊都錯 / 至少一邊錯
跨條件一律 **pooled**(該語料所有跑過的條件合起來);handoff-v16 §8.6 沒寫跨條件怎麼合,
這裡在看到任何分數之前定為 pooled(§9 記一筆)。

用法:python3 analysis/openrouter_gates.py → results/openrouter_gates.json
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import load  # noqa: E402

OUT = ROOT / "results" / "openrouter_gates.json"
B = 10000

CORPORA = {
    "exp5": {"file": "exp5/results/term_outcomes.json", "key": "term", "conds": ["M0", "M3"], "noisy": "M3",
             "weighted": True, "gemini": "Gbat", "grok": "ORgrok_zh", "scribe": "ORscribe_zh", "direct_scribe": "Sbat_bi",
             "scores": "exp5/results/scores.json"},
    "exp1": {"file": "results/noun_outcomes.json", "key": "noun", "conds": ["N0", "N1", "N2", "N3", "N4"], "noisy": "N3",
             "weighted": False, "gemini": "Abat", "grok": "ORgrok_ja", "scribe": "ORscribe_ja", "direct_scribe": "Sbat_ja_nokt",
             "grok_kt": "ORgrok_ja_kt", "scores": "results/scores.json"},
}
EXP5_ARMS = ["ORgrok", "ORscribe", "GDtrans", "ORqwen17", "ORqwen06", "ORmai2", "ORwhisperT", "ORnemo", "ORgptT", "ORmuse", "ORasm35", "ORasm36"]
GATE_B = ["ORqwen06", "ORqwen17", "ORwhisperT", "ORnemo"]


def table_for(c: dict, arms: list[str]) -> dict:
    return load({"file": c["file"], "key": c["key"], "arms": {a: a for a in arms}})


def rows(table, conds, arms):
    """(seg, n, {arm: tolerant}) — 只收所有 arm 都有的項目(缺格不當 miss)"""
    out = []
    for (cond, seg, _item), per in table.items():
        if cond in conds and all(a in per for a in arms):
            out.append((seg, per[arms[0]][0], {a: per[a][2] for a in arms}))
    return out


def recall(rs, arm, weighted):
    tot = sum(n if weighted else 1 for _s, n, _h in rs)
    hit = sum((n if weighted else 1) * h[arm] for _s, n, h in rs)
    return hit / tot if tot else None


def diff_ci(rs, a, b, weighted, seed=2026):
    """a − b 的召回差,以段為群 bootstrap"""
    by = defaultdict(list)
    for r in rs:
        by[r[0]].append(r)
    segs = sorted(by)
    rnd = random.Random(seed)
    boots = []
    for _ in range(B):
        pick = [x for _ in segs for x in by[segs[rnd.randrange(len(segs))]]]
        ra, rb = recall(pick, a, weighted), recall(pick, b, weighted)
        if ra is not None:
            boots.append(ra - rb)
    boots.sort()
    point = recall(rs, a, weighted) - recall(rs, b, weighted)
    return {"diff": round(point, 4), "ci95": [round(boots[int(.025 * len(boots))], 4), round(boots[int(.975 * len(boots)) - 1], 4)],
            "clusters": len(segs), "items": len(rs)}


def pair(rs, ded, gem, weighted):
    a_only = b_only = both_wrong = either_wrong = 0
    for _s, n, h in rs:
        w = n if weighted else 1
        ha, hb = h[ded], h[gem]
        if ha and not hb:
            a_only += w
        elif hb and not ha:
            b_only += w
        if not ha or not hb:
            either_wrong += w
        if not ha and not hb:
            both_wrong += w
    d = a_only + b_only
    return {"divergent": a_only + b_only, "ded_only": a_only, "gemini_only": b_only,
            "ded_win_rate": round(a_only / d, 4) if d else None,
            "jaccard": round(both_wrong / either_wrong, 4) if either_wrong else None}


def ter_table(scores_file: str, arms: list[str], conds: list[str]) -> dict:
    """exp5 scores.json → {arm: {(seg,cond): ter}}"""
    out = defaultdict(dict)
    for r in json.loads((ROOT / scores_file).read_text()):
        if r["arm"] in arms and r["cond"] in conds and "ter" in r:
            out[r["arm"]][(r["seg"], r["cond"])] = r["ter"]
    return out


def ter_diff_ci(tt, a, b, seed=2026):
    keys = sorted(set(tt.get(a, {})) & set(tt.get(b, {})))
    if not keys:
        return None
    segs = sorted({k[0] for k in keys})
    rnd = random.Random(seed)
    diffs = {k: tt[a][k] - tt[b][k] for k in keys}
    boots = []
    for _ in range(B):
        pick = [k for _ in segs for k in keys if k[0] == segs[rnd.randrange(len(segs))]]
        if pick:
            boots.append(statistics.mean(diffs[k] for k in pick))
    boots.sort()
    return {"diff": round(statistics.mean(diffs.values()), 4),
            "ci95": [round(boots[int(.025 * len(boots))], 4), round(boots[int(.975 * len(boots)) - 1], 4)], "files": len(keys)}


def main():
    res: dict = {"handoff": "handoff-v16.md §8.6–8.8", "pooled_across_conditions": True}
    # ── Gate A ──
    gate = {}
    for name, c in CORPORA.items():
        arms = [c["gemini"], c["grok"], c["scribe"]] + ([c["grok_kt"]] if c.get("grok_kt") else [])
        t = table_for(c, arms)
        core = [c["gemini"], c["grok"], c["scribe"]]
        rs_all = rows(t, c["conds"], core)
        rs_noisy = rows(t, [c["noisy"]], core)
        g = {
            "recall_pooled": {a: round(recall(rs_all, a, c["weighted"]), 4) for a in core},
            "recall_noisy": {a: round(recall(rs_noisy, a, c["weighted"]), 4) for a in core},
            "rule1_noisy_grok_minus_scribe": diff_ci(rs_noisy, c["grok"], c["scribe"], c["weighted"]),
            "pair_gemini_grok": pair(rs_all, c["grok"], c["gemini"], c["weighted"]),
            "pair_gemini_scribe": pair(rs_all, c["scribe"], c["gemini"], c["weighted"]),
        }
        g["rule1_pass"] = g["rule1_noisy_grok_minus_scribe"]["ci95"][1] >= 0
        g["rule2_pass"] = g["pair_gemini_grok"]["divergent"] <= g["pair_gemini_scribe"]["divergent"]
        wg, ws = g["pair_gemini_grok"]["ded_win_rate"], g["pair_gemini_scribe"]["ded_win_rate"]
        g["rule3_pass"] = (wg is not None and ws is not None and wg >= ws)
        if c.get("grok_kt"):
            rs_kt = rows(t, c["conds"], [c["gemini"], c["grok_kt"], c["scribe"]])
            g["extra_grok_kt"] = {"recall_pooled": round(recall(rs_kt, c["grok_kt"], c["weighted"]), 4),
                                  "pair_gemini_grok_kt": pair(rs_kt, c["grok_kt"], c["gemini"], c["weighted"]),
                                  "noisy_grok_kt_minus_scribe": diff_ci(rows(t, [c["noisy"]], [c["grok_kt"], c["scribe"]]),
                                                                        c["grok_kt"], c["scribe"], c["weighted"])}
        gate[name] = g
    res["gate_a"] = gate
    res["gate_a_verdict"] = ("可取代(參考級:Grok 版本未定)"
                             if all(g["rule1_pass"] and g["rule2_pass"] and g["rule3_pass"] for g in gate.values())
                             else "維持 Scribe")
    # ── Gate B(exp5)──
    c = CORPORA["exp5"]
    gb = {}
    for setting in ("auto", "zh"):
        arms = [f"{a}_{setting}" for a in EXP5_ARMS]
        t = table_for(c, arms)
        present = [a for a in arms if any(a in per for per in t.values())]
        ref = f"ORscribe_{setting}"
        tt = ter_table(c["scores"], present, c["conds"])
        tab = {}
        for a in present:
            rs = rows(t, c["conds"], [a, ref]) if ref in present else []
            tab[a] = {"recall_tolerant": round(recall(rows(t, c["conds"], [a]), a, True), 4),
                      "ter_mean": round(statistics.mean(tt[a].values()), 4) if tt.get(a) else None,
                      "vs_scribe_recall": diff_ci(rs, a, ref, True) if rs and a != ref else None,
                      "vs_scribe_ter": ter_diff_ci(tt, a, ref) if a != ref else None,
                      "gate_b_model": a.rsplit("_", 1)[0] in GATE_B}
        gb[setting] = dict(sorted(tab.items(), key=lambda kv: -(kv[1]["recall_tolerant"] or 0)))
    res["gate_b_exp5"] = gb
    # ── 經 OpenRouter 的 Scribe vs 直連 ──
    cons = {}
    for name, c in CORPORA.items():
        direct = c["direct_scribe"]
        t = table_for(c, [c["scribe"], direct])
        if not any(direct in per for per in t.values()) and name == "exp1":
            direct = "Sbat_ja"  # §8.8:Sbat_ja_nokt 不存在 → 用 Sbat_ja(帶 50 條詞表,所以差裡混了詞表)
        arms = [c["scribe"], direct]
        t = table_for(c, arms)
        rs = rows(t, c["conds"], arms)
        cons[name] = {"pair": arms, "recall": {a: round(recall(rs, a, c["weighted"]), 4) for a in arms} if rs else None,
                      "or_minus_direct": diff_ci(rs, *arms, c["weighted"]) if rs else None}
    # 同一 arm 重跑一次的雜訊(exp5;ORscribe_zh_r2 是 2026-10-15 的重跑)
    c = CORPORA["exp5"]
    t = table_for(c, ["ORscribe_zh", "ORscribe_zh_r2"])
    rs = rows(t, c["conds"], ["ORscribe_zh", "ORscribe_zh_r2"])
    if rs:
        cons["exp5_rerun_noise"] = {"pair": ["ORscribe_zh", "ORscribe_zh_r2"],
                                    "recall": {a: round(recall(rs, a, True), 4) for a in ("ORscribe_zh", "ORscribe_zh_r2")},
                                    "diff": diff_ci(rs, "ORscribe_zh", "ORscribe_zh_r2", True)}
    res["scribe_or_vs_direct"] = cons
    # ── 逐條件矩陣(描述用,不進 Gate 規則)──
    mats = {}
    for name, c, arms, err in (
        ("exp5", CORPORA["exp5"], [f"{a}_{s}" for a in EXP5_ARMS for s in ("auto", "zh")] + ["Gbat", "Sbat_bi"], "ter"),
        ("exp1", CORPORA["exp1"], [f"{a}_{s}" for a in ("ORgrok", "ORscribe", "GDtrans") for s in ("auto", "ja", "ja_kt")]
         + ["Abat", "Sbat_ja"], "cer"),
    ):
        t = table_for(c, arms)
        sc = [r for r in json.loads((ROOT / c["scores"]).read_text()) if r["cond"] in c["conds"]]
        m = {}
        for a in arms:
            if not any(a in per for per in t.values()):
                continue
            row = {cond: round(recall(rows(t, [cond], [a]), a, c["weighted"]), 4) for cond in c["conds"]
                   if rows(t, [cond], [a])}
            row["pooled"] = round(recall(rows(t, c["conds"], [a]), a, c["weighted"]), 4)
            errs = [r[err] for r in sc if r["arm"] == a and r.get(err) is not None]
            row[f"{err}_mean"] = round(statistics.mean(errs), 4) if errs else None
            row[f"{err}_median"] = round(statistics.median(errs), 4) if errs else None
            row["files"] = len(errs)
            m[a] = row
        mats[name] = m
    res["matrix"] = mats
    # GDtrans 的異常格敏感度:空輸出 3 格(exp1)、重複迴圈 1 格(exp5 GDtrans_auto T1__M3)拿掉之後
    sens = {}
    c = CORPORA["exp1"]
    bad = {("N3", "ikeda02"), ("N0", "ikeda03")}
    t = table_for(c, ["GDtrans_auto", "GDtrans_ja"])
    for a in ("GDtrans_auto", "GDtrans_ja"):
        rs = [r for r in rows(t, c["conds"], [a])]
        keep = [(seg, n, h) for (cond, seg, _i), per in t.items() if cond in c["conds"] and a in per
                and (cond, seg) not in bad for (seg, n, h) in [(seg, per[a][0], {a: per[a][2]})]]
        sens[a] = {"with_empty": round(recall(rs, a, False), 4), "without_empty_cells": round(recall(keep, a, False), 4)}
    c = CORPORA["exp5"]
    t = table_for(c, ["GDtrans_auto"])
    keep = [(seg, per["GDtrans_auto"][0], {"GDtrans_auto": per["GDtrans_auto"][2]}) for (cond, seg, _i), per in t.items()
            if cond in c["conds"] and "GDtrans_auto" in per and (cond, seg) != ("M3", "T1")]
    tt = ter_table(c["scores"], ["GDtrans_auto"], c["conds"])
    sens["GDtrans_auto_exp5"] = {"with_loop": res["gate_b_exp5"]["auto"]["GDtrans_auto"]["recall_tolerant"],
                                 "without_T1_M3": round(recall(keep, "GDtrans_auto", True), 4),
                                 "ter_mean_without_T1_M3": round(statistics.mean(v for k, v in tt["GDtrans_auto"].items()
                                                                                if k != ("T1", "M3")), 4)}
    res["gdtrans_sensitivity"] = sens
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
