#!/usr/bin/env python3
"""handoff-v12 Step 2 的對照與判讀。**純計算,不呼叫 API。**

先跑 `python3 scripts/score.py` 更新 results/scores.json 與 noun_outcomes.json,再跑這支。
判讀規則是 handoff-v12 §3 先寫死的 R1~R4,這支只把數字擺出來並機械地套規則。

輸出:results/v12_compare.json
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONDS = ["N0", "N1", "N2", "N3", "N4"]
NEW = ["G31", "G31c", "G38", "G38T", "GX", "GT", "GTv"]
BASE = ["A", "C", "Cplus", "Cplus50"]
LABEL = {
    "A": "舊 Live 3.1(8 月)", "C": "SM 即時 無詞表", "Cplus": "SM 即時 +詞表+假名",
    "Cplus50": "SM 即時 +50詞無讀音", "G31": "3.1 Live 同日(含中斷)", "G31c": "3.1 Live 同日(取代 6 檔)", "G38": "3.8 Live",
    "G38T": "3.8 Live ET(low)", "GX": "3.5 Live Translate", "GT": "3.5 Transcribe 無詞表",
    "GTv": "3.5 Transcribe +詞表",
}


def micro(rows, arm, cond=None):
    h = t = 0
    for r in rows:
        if r["arm"] == arm and (cond is None or r["cond"] == cond):
            h += r["recall_tolerant"] * r["n_nouns"]
            t += r["n_nouns"]
    return h / t if t else None


def n_files(rows, arm):
    return sum(1 for r in rows if r["arm"] == arm)


def boot_diff(outcomes, a, b, n=4000, seed=20261004):
    """配對差 a − b 的 segment-cluster bootstrap(tolerant,每項等權,與 exp1 既有做法同)。"""
    by = defaultdict(dict)
    for o in outcomes:
        if o["arm"] in (a, b):
            by[(o["seg"], o["cond"], o["noun"])][o["arm"]] = o["tolerant"]
    items = [(k[0], v[a], v[b]) for k, v in by.items() if a in v and b in v]
    if not items:
        return None
    segs = sorted({s for s, _, _ in items})
    per = defaultdict(list)
    for s, x, y in items:
        per[s].append(x - y)
    point = sum(x - y for _, x, y in items) / len(items)
    rng = random.Random(seed)
    ds = []
    for _ in range(n):
        pick = [segs[rng.randrange(len(segs))] for _ in segs]
        v = [d for s in pick for d in per[s]]
        ds.append(sum(v) / len(v))
    ds.sort()
    return {"diff": round(point, 4), "ci_lo": round(ds[int(.025 * n)], 4),
            "ci_hi": round(ds[int(.975 * n) - 1], 4), "n_items": len(items), "clusters": len(segs)}


def raw(arm):
    d = ROOT / "results" / "raw" / arm
    return [json.loads(f.read_text()) for f in sorted(d.glob("*__N*.json"))] if d.exists() else []


# handoff-v12 §6 偏離 2:G31 有 6 檔在 12 條併發下靜默中斷,併發 1 重跑(G31r)0/6 中斷
# → 依先寫死的規則判為測試假象。G31c = G31 以 G31r 取代那 6 檔;R1 與所有「− G31」的比較用 G31c。
REPLACED = {("hig01_A1", c) for c in ("N0", "N1", "N2")} | {("hig02_B12", c) for c in ("N0", "N1", "N2")}


def add_g31c(rows, outcomes):
    if not any(r["arm"] == "G31r" for r in rows):
        return
    for src, tgt in ((rows, rows), (outcomes, outcomes)):
        extra = []
        for r in src:
            if r["arm"] == "G31" and (r["seg"], r["cond"]) not in REPLACED:
                extra.append({**r, "arm": "G31c"})
            if r["arm"] == "G31r":
                extra.append({**r, "arm": "G31c"})
        tgt.extend(extra)


def main():
    rows = json.loads((ROOT / "results" / "scores.json").read_text())
    outcomes = json.loads((ROOT / "results" / "noun_outcomes.json").read_text())
    add_g31c(rows, outcomes)
    out = {"note": "handoff-v12 step 2。純計算。", "arms": {}, "diffs": {}, "rules": {}}

    print(f"{'arm':<6}{'說明':<20}{'檔':>4}{'全部':>8}" + "".join(c.rjust(7) for c in CONDS)
          + f"{'N3空':>6}{'台灣用語誤':>8}{'簡體字':>7}{'$':>7}")
    for arm in BASE + NEW:
        if not n_files(rows, arm):
            continue
        rec = {"label": LABEL[arm], "n_files": n_files(rows, arm), "recall": micro(rows, arm),
               "by_cond": {c: micro(rows, arm, c) for c in CONDS}}
        rs = [r for r in rows if r["arm"] == arm]
        rec["tw_bad"] = sum(r.get("tw_bad_hits") or 0 for r in rs) if any(r.get("tw_bad_hits") is not None for r in rs) else None
        rec["simplified"] = sum(r.get("simplified_chars") or 0 for r in rs) if any(r.get("simplified_chars") is not None for r in rs) else None
        files = raw(arm) if arm != "G31c" else []
        n3 = [f for f in files if f["file"].endswith("__N3.wav")]
        key = "input_transcription" if arm in NEW + ["A"] else "transcript"
        rec["n3_empty"] = sum(1 for f in n3 if len((f.get(key) or "").strip()) < 20)
        rec["usd"] = round(sum(f.get("usd_est", 0) for f in files), 3) if arm in NEW else None
        # 3.5 Transcribe:定稿節奏(總音訊 ÷ 定稿則數)與暫定則數
        if arm in ("GT", "GTv"):
            secs = sum(f["audio_s"] for f in files)
            nfin = sum(sum(1 for e in f["log"] if e["kind"] == "inputTranscription") for f in files)
            nint = sum(sum(1 for e in f["log"] if e["kind"] == "interimInputTranscription") for f in files)
            rec["final_every_s"] = round(secs / nfin, 2) if nfin else None
            rec["interim_per_min"] = round(nint / (secs / 60), 1) if secs else None
        if arm in ("G31", "G38", "G38T", "GX", "A"):
            fz = sorted(r["first_zh_s"] for r in rs if r.get("first_zh_s") is not None)
            rec["first_zh_p50"] = fz[len(fz) // 2] if fz else None
        out["arms"][arm] = rec
        f3 = lambda v: f"{v:.3f}" if v is not None else "—"
        print(f"{arm:<6}{LABEL[arm]:<20}{rec['n_files']:>4}{f3(rec['recall']):>8}"
              + "".join(f3(rec["by_cond"][c]).rjust(7) for c in CONDS)
              + f"{rec['n3_empty']:>6}{str(rec['tw_bad'] if rec['tw_bad'] is not None else '—'):>8}"
              f"{str(rec['simplified'] if rec['simplified'] is not None else '—'):>7}"
              f"{(f'{rec[chr(117)+chr(115)+chr(100)]:.2f}' if rec['usd'] is not None else '—'):>7}")

    print()
    pairs = [("GT", "C", "引擎(兩邊都無詞表)← 乾淨"), ("GTv", "Cplus", "引擎+讀音 ← 混變因"),
             ("GTv", "Cplus50", "Gemini 全詞表無讀音 vs SM 50 詞無讀音"), ("GTv", "GT", "Gemini 詞表的價值"),
             ("G31", "A", "同一顆 3.1,8 月 → 今天(含中斷,僅供對照)"),
             ("G31c", "A", "同一顆 3.1,8 月 → 今天(R1 用這個)"),
             ("G38", "G31c", "3.8 vs 3.1(同日)"), ("G38T", "G31c", "3.8 ET vs 3.1(同日)"),
             ("GX", "G31c", "Live Translate vs 3.1(同日)"), ("GT", "G31c", "Transcribe vs 3.1(同日)"),
             ("GTv", "G31c", "Transcribe+詞表 vs 3.1(同日)")]
    for a, b, why in pairs:
        if a in out["arms"] and b in out["arms"]:
            bd = boot_diff(outcomes, a, b)
            out["diffs"][f"{a}-{b}"] = {**(bd or {}), "why": why}
            if bd:
                print(f"  {a:>5} − {b:<8} {bd['diff']:+.3f}  CI [{bd['ci_lo']:+.3f}, {bd['ci_hi']:+.3f}]  ({why})")

    A = out["arms"]
    g = lambda k: A.get(k, {}).get("recall")
    gn3 = lambda k: A.get(k, {}).get("by_cond", {}).get("N3")
    print("\n── 判讀(handoff-v12 §3,先寫死)──")
    r1arm = "G31c" if g("G31c") is not None else "G31"
    if g(r1arm) is not None:
        drift = g(r1arm) - 0.439
        r1 = "8 月數字仍可當基準" if abs(drift) <= 0.05 else "有漂移:只用同日 G31 比"
        out["rules"]["R1"] = {"arm": r1arm, "recall": g(r1arm), "raw_G31": g("G31"), "drift": round(drift, 4), "verdict": r1}
        print(f"R1 漂移:{r1arm} {g(r1arm):.3f} − 0.439 = {drift:+.3f} → {r1}(未取代的 G31 {g('G31'):.3f})")
    if g("GT") is not None and g("GTv") is not None:
        best = "GTv" if g("GTv") >= g("GT") else "GT"
        if g("GTv") >= 0.741 and (gn3("GTv") or 0) >= 0.50:
            r2 = "與產線同級 → 選單標「可用」"
        elif g("GT") >= 0.639:
            r2 = "引擎不輸無詞表 SM → 選單標「對照」"
        else:
            r2 = "選單標「僅供對照,實用不建議」"
        out["rules"]["R2"] = {"GT": g("GT"), "GTv": g("GTv"), "GTv_N3": gn3("GTv"), "use": best, "verdict": r2}
        print(f"R2 耳朵:GT {g('GT'):.3f} / GTv {g('GTv'):.3f}(N3 {gn3('GTv'):.3f})→ {r2};選單用 {best}")
    e2e = [k for k in ("G38", "G38T", "GX") if g(k) is not None]
    if e2e:
        top = max(e2e, key=g)
        ok = g(top) >= 0.70 and (gn3(top) or 0) >= 0.40
        r3 = "加入選單(第四個選項)" if ok else "不加,只記錄"
        out["rules"]["R3"] = {"best": top, "recall": g(top), "N3": gn3(top), "verdict": r3}
        print(f"R3 一體式:最佳 {top} {g(top):.3f}(N3 {gn3(top):.3f})→ {r3}")
    cand = [k for k in NEW if g(k) is not None and k != "G31"]
    if cand and g("Cplus") is not None:
        top = max(cand, key=g)
        hit = g(top) - g("Cplus") >= 0.05 and (gn3(top) or 0) >= (gn3("Cplus") or 0)
        r4 = "建議另開任務重評主線" if hit else "主線不動"
        out["rules"]["R4"] = {"best": top, "diff_vs_Cplus": round(g(top) - g("Cplus"), 4), "verdict": r4}
        print(f"R4 主線:最佳 {top} − Cplus = {g(top) - g('Cplus'):+.3f} → {r4}")
    tot = sum(A[k]["usd"] for k in NEW if k in A and A[k].get("usd") is not None and k != "G31c")
    tot += sum(f.get("usd_est", 0) for f in raw("G31r"))
    out["total_usd_est"] = round(tot, 3)
    print(f"\n牌價估計合計 ${tot:.2f}(保險絲 $8)")
    (ROOT / "results" / "v12_compare.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print("→ results/v12_compare.json")


if __name__ == "__main__":
    sys.exit(main())
