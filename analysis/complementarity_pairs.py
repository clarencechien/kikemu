#!/usr/bin/env python3
"""handoff-v11 §2:非即時雙跑**配對**的互補性(取代架構家族推論)。

**純計算,不呼叫任何 API。** 輸入是既有的 per-item outcome 檔。

要回答的問題:非即時線的雙跑要配 **SM + Gemini** 還是 **Scribe + Gemini**?
原本的建議是從「架構家族距離」推論出來的
(`docs/offline-meeting-architecture.md` §3),而任務書 §1 指出那條推理鏈的弱點:
**Scribe 的解碼器架構未公開,「偏生成式」是猜的。**

拿一個猜測的標籤,去推導一個可以直接量的東西——那就量它。

三對(任務書 §2.1):

    SM 批次 × Gemini 批次        暫定推薦
    Scribe 批次 × Gemini 批次    挑戰者
    SM 批次 × Scribe 批次        兩個專用引擎的距離

判讀規則寫在 handoff-v11 §5(**先寫死**),這支只負責把數字算出來並套規則。

⚠️ **加權方式不是自由選擇。** exp5 的每一列是一個術語**型**,`n` 是實例數;
報告的召回率是**實例加權**的。任務書 §4 要求「單一 arm 的 oracle 必須等於
報告中該 arm 的召回率」——那條檢查**強制**我們用實例加權。
(bootstrap 那次沒乘 n,算出來連正負號都不一樣,見 `exp5/scripts/bootstrap_e4b.py`。)

用法:
    python3 analysis/complementarity_pairs.py
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from oracle import EXPERIMENTS, load, recall  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT_JSON = ROOT / "results" / "pairing.json"

# ── 要比的三對 ────────────────────────────────────────────────────────────
# Gemini 那一格取 **Gbat(3.5-flash)**,因為架構文件 §6 的成本 $0.26/hr 就是它;
# Gbat37 是 $2.69/hr(10 倍),另外當敏感度分析報,不進主判讀。
SETUPS = {
    "exp5": {
        "label": "exp5 領域外(中英夾雜,3 段 × M0/M3,實例加權)",
        "primary": True,          # 任務書 §5:以 exp5 為主
        "gemini": "Gbat", "sm": "Xbat_bi", "scribe": "Sbat_bi",
        "alt_gemini": "Gbat37",
    },
    "exp1": {
        "label": "exp1 日文導覽(6 段 × N0–N4,每項等權)",
        "primary": False,
        "gemini": "Abat", "sm": "Cbplus", "scribe": "Sbat_ja_full",
        "alt_gemini": None,
    },
}


def pairs_of(cfg: dict) -> list[tuple[str, str, str]]:
    g, s, k = cfg["gemini"], cfg["sm"], cfg["scribe"]
    return [("SM × Gemini", s, g), ("Scribe × Gemini", k, g), ("SM × Scribe", s, k)]


# ── §2.1 Jaccard ─────────────────────────────────────────────────────────
def jaccard(table, a, b, cond, judge, weighted):
    """J = |兩者都錯| / |至少一者錯|。

    weighted=True 時以實例數 n 加權(exp5);False 時每項等權(exp1)。
    **兩種都報**,因為型平均與實例加權在這個 repo 出過事。
    """
    j = 1 if judge == "strict" else 2
    both = either = 0
    for (c, _s, _i), per in table.items():
        if c != cond or a not in per or b not in per:
            continue
        w = per[a][0] if weighted else 1
        ea, eb = not per[a][j], not per[b][j]
        if ea or eb:
            either += w
        if ea and eb:
            both += w
    return (both / either) if either else None, either


# ── §2.3 勝率:只看兩邊判定不同的項目 ────────────────────────────────────
def split_rate(table, a, b, cond, judge, weighted):
    j = 1 if judge == "strict" else 2
    a_only = b_only = 0
    for (c, _s, _i), per in table.items():
        if c != cond or a not in per or b not in per:
            continue
        w = per[a][0] if weighted else 1
        ha, hb = per[a][j], per[b][j]
        if ha and not hb:
            a_only += w
        elif hb and not ha:
            b_only += w
    n = a_only + b_only
    return {"a_only": a_only, "b_only": b_only, "divergent": n,
            "a_win_rate": round(a_only / n, 3) if n else None}


# ── §4 正確性檢查:單一 arm 的 oracle 必須等於報告的召回率 ────────────────
def report_recall(exp: str) -> dict:
    """從 scores.json 獨立重算,當作對照組(不是從 outcome 檔算的)。"""
    f = {"exp1": "results/scores.json", "exp5": "exp5/results/scores.json"}[exp]
    field = {"exp1": "n_nouns", "exp5": "n_inst"}[exp]
    agg = defaultdict(lambda: [0.0, 0.0])
    for r in json.loads((ROOT / f).read_text()):
        for judge, col in (("strict", "recall_strict"), ("tolerant", "recall_tolerant")):
            if col not in r:
                continue
            k = (r["arm"], r["cond"], judge)
            agg[k][0] += r[col] * r[field]
            agg[k][1] += r[field]
    return {k: v[0] / v[1] for k, v in agg.items() if v[1]}


# ── pooled(所有條件合起來)+ segment-cluster bootstrap ──────────────────
#
# 任務書 §5 的判讀順序沒有寫「跨條件怎麼合併」(規則缺口,見報告 §6)。
# 逐條件判會出現條件之間互相矛盾的情形,所以**兩種都算**:逐條件 + pooled。
#
# 規則 2 寫「差距 < 0.05(**在 bootstrap CI 內重疊**)」,所以 CI 要真的算,
# 不能只比 0.05。重抽單位是 **segment**(同一段裡的術語不獨立)。
# ⚠️ exp5 只有 3 個 segment、exp1 只有 6 個——**cluster 數極少,CI 會很寬**,
# 這本身就是「打平」判定的實質理由。
def recall_pooled(table, arms, conds, judge):
    """所有條件合起來的 micro 召回(缺格照樣不算 miss)。"""
    j = 1 if judge == "strict" else 2
    hit = tot = 0
    for (c, _seg, _item), per in table.items():
        if c not in conds or not all(a in per for a in arms):
            continue
        n = per[arms[0]][0]
        tot += n
        if any(per[a][j] for a in arms):
            hit += n
    return (hit / tot if tot else None), tot


def pooled_items(table, conds, a, b, judge, weighted):
    """→ [(seg, w, a錯, b錯, a對, b對)],給 pooled 與 bootstrap 共用。"""
    j = 1 if judge == "strict" else 2
    rows = []
    for (c, seg, _i), per in table.items():
        if c not in conds or a not in per or b not in per:
            continue
        w = per[a][0] if weighted else 1
        rows.append((seg, w, not per[a][j], not per[b][j], per[a][j], per[b][j]))
    return rows


def j_from(rows) -> float | None:
    both = either = 0
    for _seg, w, ea, eb, _ha, _hb in rows:
        if ea or eb:
            either += w
        if ea and eb:
            both += w
    return (both / either) if either else None


def bootstrap_dj(table, conds, pair_a, pair_b, judge, weighted, n=4000, seed=20260820):
    """ΔJ = J(pair_b) − J(pair_a) 的 segment-cluster bootstrap CI。

    負值代表 pair_b 比較互補。CI 含 0 → 依規則 2 視為打平。
    """
    import random
    ra = pooled_items(table, conds, *pair_a, judge, weighted)
    rb = pooled_items(table, conds, *pair_b, judge, weighted)
    segs = sorted({r[0] for r in ra} | {r[0] for r in rb})
    by_a, by_b = defaultdict(list), defaultdict(list)
    for r in ra:
        by_a[r[0]].append(r)
    for r in rb:
        by_b[r[0]].append(r)
    rng = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [segs[rng.randrange(len(segs))] for _ in segs]
        sa = [x for s in pick for x in by_a[s]]
        sb = [x for s in pick for x in by_b[s]]
        ja, jb = j_from(sa), j_from(sb)
        if ja is not None and jb is not None:
            ds.append(jb - ja)
    if not ds:
        return None
    ds.sort()
    return {"delta_point": round((j_from(rb) or 0) - (j_from(ra) or 0), 4),
            "ci_lo": round(ds[int(0.025 * len(ds))], 4),
            "ci_hi": round(ds[int(0.975 * len(ds)) - 1], 4),
            "n_clusters": len(segs),
            "ci_includes_zero": ds[int(0.025 * len(ds))] <= 0 <= ds[int(0.975 * len(ds)) - 1]}


def main() -> None:
    out = {"note": "handoff-v11:非即時雙跑配對的互補性。純計算,無 API 呼叫。",
           "weighting": {"exp5": "實例加權(n),與報告召回率一致",
                         "exp1": "每項等權(該檔無 n 欄)"},
           "correctness_check": {}, "pairs": {}, "three_way": {}}
    ok_all = True

    for exp, cfg in SETUPS.items():
        spec = EXPERIMENTS[exp]
        table = load(spec)
        conds = spec["conds"]
        weighted = exp == "exp5"
        rep = report_recall(exp)
        arms = [cfg["gemini"], cfg["sm"], cfg["scribe"]]

        # ── §4 ────────────────────────────────────────────────────────────
        chk = {}
        for a in arms:
            for cond in conds:
                for judge in ("strict", "tolerant"):
                    r, n = recall(table, [a], cond, judge)
                    ref = rep.get((a, cond, judge))
                    if r is None or ref is None:
                        continue
                    good = abs(r - ref) < 5e-4
                    ok_all &= good
                    chk[f"{a}/{cond}/{judge}"] = {
                        "oracle_single": round(r, 4), "report": round(ref, 4),
                        "match": good, "n": n}
        out["correctness_check"][exp] = chk

        # ── §2.1 / §2.2 / §2.3 ───────────────────────────────────────────
        block = {"label": cfg["label"], "primary": cfg["primary"], "conds": {}}
        for cond in conds:
            cell = {}
            for name, a, b in pairs_of(cfg):
                per_judge = {}
                for judge in ("strict", "tolerant"):
                    jw, n_either = jaccard(table, a, b, cond, judge, weighted)
                    ju, _ = jaccard(table, a, b, cond, judge, False)
                    ra, _ = recall(table, [a], cond, judge)
                    rb, _ = recall(table, [b], cond, judge)
                    rp, _ = recall(table, [a, b], cond, judge)
                    if None in (jw, ra, rb, rp):
                        continue
                    best = max(ra, rb)
                    per_judge[judge] = {
                        "jaccard": round(jw, 4),
                        "jaccard_unweighted": round(ju, 4) if ju is not None else None,
                        "single": {a: round(ra, 4), b: round(rb, 4)},
                        "best_single": round(best, 4),
                        "pair_oracle": round(rp, 4),
                        "gain_over_best": round(rp - best, 4),
                        "n_either_wrong": n_either,
                        **split_rate(table, a, b, cond, judge, weighted)}
                if per_judge:
                    cell[name] = {"arms": [a, b], **per_judge}
            if cell:
                block["conds"][cond] = cell
        out["pairs"][exp] = block

        # ── §6 三跑 ───────────────────────────────────────────────────────
        tw = {}
        for cond in conds:
            row = {}
            for judge in ("strict", "tolerant"):
                r3, _ = recall(table, arms, cond, judge)
                best_pair = None
                for name, a, b in pairs_of(cfg):
                    rp, _ = recall(table, [a, b], cond, judge)
                    if rp is not None and (best_pair is None or rp > best_pair[1]):
                        best_pair = (name, rp)
                if r3 is None or best_pair is None:
                    continue
                row[judge] = {"three_way_oracle": round(r3, 4),
                              "best_pair": best_pair[0],
                              "best_pair_oracle": round(best_pair[1], 4),
                              "gain": round(r3 - best_pair[1], 4)}
            if row:
                tw[cond] = row
        out["three_way"][exp] = tw

        # ── pooled + bootstrap ───────────────────────────────────────────
        pool = {}
        for judge in ("strict", "tolerant"):
            per_pair = {}
            for name, a, b in pairs_of(cfg):
                rows = pooled_items(table, conds, a, b, judge, weighted)
                ra, _ = recall_pooled(table, [a], conds, judge)
                rb, _ = recall_pooled(table, [b], conds, judge)
                rp, _ = recall_pooled(table, [a, b], conds, judge)
                best = max(ra, rb)
                a_only = sum(w for _s, w, _ea, _eb, ha, hb in rows if ha and not hb)
                b_only = sum(w for _s, w, _ea, _eb, ha, hb in rows if hb and not ha)
                nd = a_only + b_only
                per_pair[name] = {
                    "jaccard": round(j_from(rows), 4),
                    "best_single": round(best, 4),
                    "pair_oracle": round(rp, 4),
                    "gain_over_best": round(rp - best, 4),
                    "divergent": nd,
                    "a_win_rate": round(a_only / nd, 3) if nd else None}
            per_pair["ΔJ(Scribe×Gemini − SM×Gemini)"] = bootstrap_dj(
                table, conds, (cfg["sm"], cfg["gemini"]),
                (cfg["scribe"], cfg["gemini"]), judge, weighted)
            # §6:三跑 pooled
            r3, _ = recall_pooled(table, arms, conds, judge)
            bp = max(((n, per_pair[n]["pair_oracle"]) for n, _a, _b in pairs_of(cfg)),
                     key=lambda x: x[1])
            per_pair["三跑"] = {"three_way_oracle": round(r3, 4),
                                "best_pair": bp[0], "best_pair_oracle": bp[1],
                                "gain": round(r3 - bp[1], 4)}
            pool[judge] = per_pair
        out.setdefault("pooled", {})[exp] = pool

        # ── 敏感度:Gemini 換 3.7 ─────────────────────────────────────────
        if cfg["alt_gemini"]:
            alt = {}
            for cond in conds:
                row = {}
                for label, other in (("SM × Gemini3.7", cfg["sm"]),
                                     ("Scribe × Gemini3.7", cfg["scribe"])):
                    jw, _ = jaccard(table, other, cfg["alt_gemini"], cond,
                                    "tolerant", weighted)
                    if jw is not None:
                        row[label] = round(jw, 4)
                if row:
                    alt[cond] = row
            out.setdefault("sensitivity_alt_gemini", {})[exp] = alt

    out["correctness_check"]["all_match"] = ok_all
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1))

    # ── 印出來 ────────────────────────────────────────────────────────────
    print("handoff-v11:非即時雙跑配對\n")
    print(f"§4 正確性檢查(單一 arm oracle == 報告召回率):"
          f"{'✅ 全部相符' if ok_all else '❌ 有不符,先修再往下看'}\n")

    for exp, block in out["pairs"].items():
        print(f"── {block['label']}"
              f"{'  ← 任務書 §5 指定的主判讀語料' if block['primary'] else ''}")
        for judge in ("tolerant", "strict"):
            print(f"\n  [{judge}]  J = 兩者都錯 / 至少一者錯(越低越互補)")
            names = list(next(iter(block["conds"].values())).keys())
            print("  " + "cond".ljust(6) + "".join(n.rjust(20) for n in names))
            for cond, cell in block["conds"].items():
                line = "  " + cond.ljust(6)
                for n in names:
                    v = cell.get(n, {}).get(judge)
                    line += (f"{v['jaccard']:.3f}".rjust(20) if v else "—".rjust(20))
                print(line)
        print()
    print("→", OUT_JSON.relative_to(ROOT))


if __name__ == "__main__":
    main()
