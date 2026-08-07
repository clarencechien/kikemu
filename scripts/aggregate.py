#!/usr/bin/env python3
"""Aggregate scores into the report's tables with uncertainty quantification.

- Recall matrices (tolerant primary, strict secondary): micro-average over
  all nouns, per arm x condition.
- Key deltas (C+ - C, A - C+) per condition with segment-cluster bootstrap
  95% CIs (10k resamples, seed fixed) — segments are the sampling unit
  because noun outcomes within a segment are correlated.
- Paired per-noun McNemar counts per condition.
- CER / latency / rewrite / TW-term / adequacy summaries.
Writes results/summary.json and prints markdown tables.
"""
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
CONDS = ["N0", "N1", "N2", "N3", "N4"]
ARMS = ["A", "C", "Cplus", "Cb", "Cbplus"]
BOOT = 10000
SEED = 12345


def load():
    rows = json.loads((RES / "scores.json").read_text())
    outcomes = json.loads((RES / "noun_outcomes.json").read_text())
    return rows, outcomes


def recall_matrix(outcomes, key):
    m = defaultdict(lambda: [0, 0])
    for o in outcomes:
        cell = m[(o["arm"], o["cond"])]
        cell[0] += o[key]
        cell[1] += 1
    return {k: v[0] / v[1] for k, v in m.items() if v[1]}


def bootstrap_delta(outcomes, arm_a, arm_b, cond, key):
    """Segment-cluster bootstrap CI for pooled recall(arm_a) - recall(arm_b)."""
    by_seg = defaultdict(lambda: defaultdict(list))
    for o in outcomes:
        if o["cond"] == cond and o["arm"] in (arm_a, arm_b):
            by_seg[o["seg"]][o["arm"]].append(o[key])
    segs = sorted(by_seg)
    rng = random.Random(SEED)
    deltas = []
    for _ in range(BOOT):
        pick = [segs[rng.randrange(len(segs))] for _ in segs]
        na = sum(sum(by_seg[s][arm_a]) for s in pick)
        da = sum(len(by_seg[s][arm_a]) for s in pick)
        nb = sum(sum(by_seg[s][arm_b]) for s in pick)
        db = sum(len(by_seg[s][arm_b]) for s in pick)
        if da and db:
            deltas.append(na / da - nb / db)
    deltas.sort()
    point_a = sum(sum(by_seg[s][arm_a]) for s in segs) / max(1, sum(len(by_seg[s][arm_a]) for s in segs))
    point_b = sum(sum(by_seg[s][arm_b]) for s in segs) / max(1, sum(len(by_seg[s][arm_b]) for s in segs))
    return {
        "delta": round(point_a - point_b, 4),
        "ci95": [round(deltas[int(len(deltas) * 0.025)], 4), round(deltas[int(len(deltas) * 0.975)], 4)],
    }


def mcnemar(outcomes, arm_a, arm_b, cond, key):
    d = {}
    for o in outcomes:
        if o["cond"] == cond and o["arm"] in (arm_a, arm_b):
            d.setdefault((o["seg"], o["noun"]), {})[o["arm"]] = o[key]
    b = sum(1 for v in d.values() if v.get(arm_a) == 1 and v.get(arm_b) == 0)
    c = sum(1 for v in d.values() if v.get(arm_a) == 0 and v.get(arm_b) == 1)
    return {"a_only": b, "b_only": c}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def main():
    rows, outcomes = load()
    summary = {}
    for key in ("tolerant", "strict"):
        summary[f"recall_{key}"] = {
            f"{arm}|{cond}": round(v, 4)
            for (arm, cond), v in recall_matrix(outcomes, key).items()
        }
    present = {o["arm"] for o in outcomes}
    summary["deltas"] = {}
    # realtime-vs-batch penalty and dictionary value under batch
    if {"Cb", "Cbplus"} <= present:
        summary["batch"] = {
            cond: {
                "Cb_minus_C_tolerant": bootstrap_delta(outcomes, "Cb", "C", cond, "tolerant"),
                "Cbplus_minus_Cplus_tolerant": bootstrap_delta(outcomes, "Cbplus", "Cplus", cond, "tolerant"),
                "Cbplus_minus_Cb_tolerant": bootstrap_delta(outcomes, "Cbplus", "Cb", cond, "tolerant"),
                "A_minus_Cbplus_tolerant": bootstrap_delta(outcomes, "A", "Cbplus", cond, "tolerant"),
            }
            for cond in CONDS
        }
    for cond in CONDS:
        summary["deltas"][cond] = {
            "Cplus_minus_C_tolerant": bootstrap_delta(outcomes, "Cplus", "C", cond, "tolerant"),
            "A_minus_Cplus_tolerant": bootstrap_delta(outcomes, "A", "Cplus", cond, "tolerant"),
            "A_minus_C_tolerant": bootstrap_delta(outcomes, "A", "C", cond, "tolerant"),
            "Cplus_minus_C_strict": bootstrap_delta(outcomes, "Cplus", "C", cond, "strict"),
            "A_minus_Cplus_strict": bootstrap_delta(outcomes, "A", "Cplus", cond, "strict"),
            "mcnemar_Cplus_vs_C_tolerant": mcnemar(outcomes, "Cplus", "C", cond, "tolerant"),
            "mcnemar_A_vs_Cplus_tolerant": mcnemar(outcomes, "A", "Cplus", cond, "tolerant"),
        }
    # CER / latency / rewrite / tw
    for metric in ("cer", "rewrite_rate", "tw_bad_hits", "simplified_chars"):
        summary[metric] = {
            f"{arm}|{cond}": mean([r.get(metric) for r in rows if r["arm"] == arm and r["cond"] == cond])
            for arm in ARMS
            for cond in CONDS
        }
    summary["latency"] = {
        "C_final_p50": mean([r.get("final_p50") for r in rows if r["arm"] == "C"]),
        "C_final_p90": mean([r.get("final_p90") for r in rows if r["arm"] == "C"]),
        "C_partial_p50": mean([r.get("partial_p50") for r in rows if r["arm"] == "C"]),
        "Cplus_final_p50": mean([r.get("final_p50") for r in rows if r["arm"] == "Cplus"]),
        "Cplus_partial_p50": mean([r.get("partial_p50") for r in rows if r["arm"] == "Cplus"]),
        "A_first_zh_s": mean([r.get("first_zh_s") for r in rows if r["arm"] == "A"]),
        "A_tail_after_audio_end_s": mean([r.get("tail_after_audio_end_s") for r in rows if r["arm"] == "A"]),
    }
    # adequacy
    jf = RES / "judge_scores.json"
    if jf.exists():
        js = json.loads(jf.read_text())
        adq = defaultdict(list)
        twl = defaultdict(list)
        for j in js:
            adq[(j["arm"], j["cond"])].append(j["adequacy"])
            twl[(j["arm"], j["cond"])].append(j["tw_locale"])
        summary["adequacy"] = {f"{a}|{c}": mean(v) for (a, c), v in adq.items()}
        summary["tw_locale"] = {f"{a}|{c}": mean(v) for (a, c), v in twl.items()}

    (RES / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))

    # markdown matrices
    def matrix_md(title, data):
        lines = [f"### {title}", "| arm | " + " | ".join(CONDS) + " |", "|---|" + "---|" * len(CONDS)]
        for arm in ARMS:
            cells = [data.get(f"{arm}|{c}") for c in CONDS]
            lines.append(f"| {arm} | " + " | ".join("—" if v is None else f"{v:.3f}" for v in cells) + " |")
        return "\n".join(lines)

    print(matrix_md("Recall (tolerant)", summary["recall_tolerant"]))
    print()
    print(matrix_md("Recall (strict)", summary["recall_strict"]))
    print()
    print(matrix_md("CER", summary["cer"]))
    print()
    print(json.dumps(summary["deltas"], indent=1))
    print(json.dumps(summary["latency"], indent=1))


if __name__ == "__main__":
    main()
