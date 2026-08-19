#!/usr/bin/env python3
"""handoff-v7 §2:跨引擎 oracle 天花板與錯誤互補性。

**純計算,不呼叫任何 API、不碰音檔。** 全部輸入來自既有的 per-item outcome 檔:

    results/noun_outcomes.json          exp1(日文導覽,67 專名)
    exp2/results/term_outcomes.json     exp2(中英夾雜,86 術語實例)
    exp5/results/term_outcomes.json     exp5(領域外,128 術語實例)

要回答的是「正確答案在不在候選集裡」——若各引擎錯在同一批詞上,
融合/GER 沒有空間可拿。

用法:
    python3 analysis/oracle.py            # 三個實驗全跑,寫進 results/
"""
import itertools
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 主分析只放「同一份音訊上可以同時跑」的 arm。
# exp1 的 *_pp_*(前處理)與 *_dir_*(指向性)改的是**輸入**不是引擎——
# 現場只有一支麥克風,把它們並進 oracle 等於假設你同時架了三種麥,
# 會虛增天花板。它們另外算,結果標成參考用。
EXPERIMENTS = {
    "exp1": {
        "file": "results/noun_outcomes.json",
        "key": "noun",
        "arms": {"A": "Gemini Live", "Abat": "Gemini 批次",
                 "C": "SM 即時", "Cplus": "SM 即時+詞表",
                 "Cb": "SM 批次", "Cbplus": "SM 批次+詞表"},
        "conds": ["N0", "N1", "N2", "N3", "N4"],
        "clean_ref": True,   # 參考來自大阪觀光局頁面,無主場偏差
    },
    "exp2": {
        "file": "exp2/results/term_outcomes.json",
        "key": "term",
        "arms": {"G": "Gemini Live", "X_bi": "SM 雙語", "X_cmn": "SM 單語",
                 "Xsli_bi": "SM 雙語+投影片詞表", "Xdom_bi": "SM 雙語+領域詞表",
                 "Xbrz_auto": "Breeze"},
        "conds": ["M0", "M1", "M2", "M3"],
        "clean_ref": False,  # 參考由雙 Gemini 一致性建立
    },
    "exp5": {
        "file": "exp5/results/term_outcomes.json",
        "key": "term",
        "arms": {"G": "Gemini Live", "Gbat": "Gemini 3.5 批次",
                 "Gbat37": "Gemini 3.7 批次", "Xbat_bi": "SM 批次",
                 "X_bi": "SM 即時", "Xbrz_auto": "Breeze"},
        "conds": ["M0", "M3"],
        "clean_ref": False,  # 參考由 gemini-3.5-flash 產生 → Gemini 系有主場偏差
    },
}

# exp5 的「乾淨組」:把與參考同源的 Gemini 全部排除,只剩兩個外人。
EXP5_CLEAN_ARMS = ["Xbrz_auto", "Xbat_bi"]


def load(spec: dict):
    """→ {(cond, seg, item): {arm: (n, strict, tolerant)}}"""
    rows = json.loads((ROOT / spec["file"]).read_text())
    table: dict = {}
    for r in rows:
        if r["arm"] not in spec["arms"]:
            continue
        k = (r["cond"], r["seg"], r[spec["key"]])
        table.setdefault(k, {})[r["arm"]] = (
            r.get("n", 1), int(r["strict"]), int(r["tolerant"]))
    return table


def recall(table, arms, cond, judge):
    """arms 取 OR 之後的 micro-average 召回;只算這些 arm **全都有跑** 的項目。

    缺格不能當成 miss——那會把「沒跑」講成「聽錯」。
    """
    j = 1 if judge == "strict" else 2
    hit = tot = 0
    for (c, _seg, _item), per in table.items():
        if c != cond or not all(a in per for a in arms):
            continue
        n = per[arms[0]][0]
        tot += n
        if any(per[a][j] for a in arms):
            hit += n
    return (hit / tot if tot else None), tot


def covered_arms(table, all_arms, cond):
    """這個條件下有資料的 arm(exp1 的 Abat 只跑 N0/N3/N4)"""
    seen = {a for (c, _, _), per in table.items() if c == cond for a in per}
    return [a for a in all_arms if a in seen]


def analyse(name: str, spec: dict, arms: list[str] | None = None, tag: str = ""):
    table = load(spec)
    arms = arms or list(spec["arms"])
    out = {"experiment": name, "tag": tag, "clean_ref": spec["clean_ref"],
           "arms": {a: spec["arms"][a] for a in arms}, "conds": {}}

    for cond in spec["conds"]:
        av = covered_arms(table, arms, cond)
        if len(av) < 2:
            continue
        cell = {"arms_present": av, "single": {}, "pairs": {}, "all": {}}
        for judge in ("strict", "tolerant"):
            singles = {}
            for a in av:
                r, n = recall(table, [a], cond, judge)
                singles[a] = round(r, 4) if r is not None else None
            cell["single"][judge] = singles
            best = max(singles.values())
            cell.setdefault("best_single", {})[judge] = round(best, 4)

            r_all, n_all = recall(table, av, cond, judge)
            cell["all"][judge] = {"oracle": round(r_all, 4),
                                  "gain_over_best": round(r_all - best, 4),
                                  "n_items": n_all}

            pairs = {}
            for a, b in itertools.combinations(av, 2):
                r, _ = recall(table, [a, b], cond, judge)
                base = max(singles[a], singles[b])
                pairs[f"{a}+{b}"] = {"oracle": round(r, 4),
                                     "gain_over_best_of_pair": round(r - base, 4)}
            cell["pairs"][judge] = pairs
        out["conds"][cond] = cell
    return out, table


def recoverable(table, arms, spec, judge="tolerant"):
    """oracle 命中但「最佳單一 arm」沒中的項目——逐筆列出,比率會騙人,清單不會。"""
    j = 1 if judge == "strict" else 2
    rows = []
    for cond in spec["conds"]:
        av = covered_arms(table, arms, cond)
        if len(av) < 2:
            continue
        singles = {a: recall(table, [a], cond, judge)[0] for a in av}
        best = max(singles, key=lambda a: singles[a])
        for (c, seg, item), per in sorted(table.items()):
            if c != cond or not all(a in per for a in av):
                continue
            if per[best][j]:
                continue
            hits = [a for a in av if per[a][j]]
            if hits:
                rows.append({"cond": cond, "seg": seg, "item": item,
                             "n": per[av[0]][0], "best_arm": best,
                             "saved_by": hits})
    return rows


def unrecoverable(table, arms, spec, judge="tolerant"):
    """所有 arm 都錯的項目——任何後處理都救不回,這批詞定義真正的天花板。"""
    j = 1 if judge == "strict" else 2
    rows = []
    for cond in spec["conds"]:
        av = covered_arms(table, arms, cond)
        if len(av) < 2:
            continue
        for (c, seg, item), per in sorted(table.items()):
            if c != cond or not all(a in per for a in av):
                continue
            if not any(per[a][j] for a in av):
                rows.append({"cond": cond, "seg": seg, "item": item,
                             "n": per[av[0]][0]})
    return rows


def jaccard(table, arms, spec, judge="tolerant"):
    """J = |兩者都錯| / |至少一者錯|。接近 1 → 錯在同一批詞 → 融合無效。"""
    j = 1 if judge == "strict" else 2
    out = {}
    for cond in spec["conds"]:
        av = covered_arms(table, arms, cond)
        if len(av) < 2:
            continue
        cell = {}
        for a, b in itertools.combinations(av, 2):
            both = either = 0
            for (c, _s, _i), per in table.items():
                if c != cond or a not in per or b not in per:
                    continue
                ea, eb = not per[a][j], not per[b][j]
                if ea or eb:
                    either += 1
                if ea and eb:
                    both += 1
            cell[f"{a}+{b}"] = round(both / either, 4) if either else None
        out[cond] = cell
    return out


def main() -> None:
    (ROOT / "analysis").mkdir(exist_ok=True)
    result = {"note": "handoff-v7 §2 跨引擎 oracle。純計算,無 API 呼叫。",
              "runs": []}
    md_rec, md_unrec = [], []

    runs = [("exp1", EXPERIMENTS["exp1"], None, "全 arm"),
            ("exp2", EXPERIMENTS["exp2"], None, "全 arm"),
            ("exp5", EXPERIMENTS["exp5"], None, "全 arm(含 Gemini,有主場偏差)"),
            ("exp5", EXPERIMENTS["exp5"], EXP5_CLEAN_ARMS,
             "乾淨組(Breeze + SM,排除與參考同源的 Gemini)")]

    for name, spec, arms, tag in runs:
        out, table = analyse(name, spec, arms, tag)
        arms_used = arms or list(spec["arms"])
        out["jaccard_tolerant"] = jaccard(table, arms_used, spec)
        rec = recoverable(table, arms_used, spec)
        unrec = unrecoverable(table, arms_used, spec)
        out["n_recoverable"] = len(rec)
        out["n_unrecoverable"] = len(unrec)
        result["runs"].append(out)

        md_rec.append(f"## {name} — {tag}\n")
        if not rec:
            md_rec.append("(無)\n")
        for r in rec:
            md_rec.append(f"- `{r['cond']}` **{r['item']}** ×{r['n']}({r['seg']})"
                          f" — 最佳單一 arm `{r['best_arm']}` 沒中,"
                          f"救回者:{', '.join(r['saved_by'])}")
        md_rec.append("")
        md_unrec.append(f"## {name} — {tag}\n")
        if not unrec:
            md_unrec.append("(無)\n")
        for r in unrec:
            md_unrec.append(f"- `{r['cond']}` **{r['item']}** ×{r['n']}({r['seg']})")
        md_unrec.append("")

    (ROOT / "results/oracle.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1))
    (ROOT / "results/oracle_recoverable.md").write_text(
        "# Oracle 可救回清單(tolerant)\n\n"
        "「oracle 命中,但該條件下最佳單一 arm 沒命中」的項目。\n"
        "**要人眼看:救回來的是不是真的重要的詞。比率會騙人,清單不會。**\n\n"
        + "\n".join(md_rec))
    (ROOT / "results/oracle_unrecoverable.md").write_text(
        "# Oracle 救不回清單(tolerant)\n\n"
        "所有 arm 都錯的項目。任何後處理都救不回——**這批詞定義真正的天花板**。\n"
        "若集中在某個聲學條件,那是拾音問題,不是模型問題。\n\n"
        + "\n".join(md_unrec))
    print(f"oracle.json / oracle_recoverable.md / oracle_unrecoverable.md 已寫入 results/")


if __name__ == "__main__":
    main()
