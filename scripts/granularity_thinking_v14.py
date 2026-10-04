#!/usr/bin/env python3
"""handoff-v14:整段 vs 逐句 × thinking 預設 vs minimal,拆解「產品逐句 4.06 vs exp1 整段 4.72」。

判讀規則在 handoff-v14.md §3(先 commit 才跑)。呼叫、帳本、評審 PROMPT 都沿用 v13 / exp1 的實作。

  python3 scripts/granularity_thinking_v14.py translate   # W-def、W-min(12 檔)、S-def(逐句)
  python3 scripts/granularity_thinking_v14.py judge       # 三格 × 12 檔 × 3 評審(S-min 沿用 v13)
  python3 scripts/granularity_thinking_v14.py analyze     # → results/granularity_thinking_v14.json

保險絲 BUDGET_USD(預設 4.00),帳本 results/raw/_v14_ledger.json。MAX_CALLS=N 只打前 N 筆(對價用)。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("v13", ROOT / "scripts" / "context_translate_v13.py")
v13 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v13)

# 帳本與保險絲換成 v14 自己的(v13 的 call/charge 讀模組全域)
v13.LEDGER = ROOT / "results" / "raw" / "_v14_ledger.json"
v13.BUDGET_USD = float(os.environ.get("BUDGET_USD", "4.0"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MAX_CALLS = int(os.environ.get("MAX_CALLS", "0"))

RAW = ROOT / "results" / "raw" / "v14"
OUT = ROOT / "results" / "granularity_thinking_v14.json"
CONDS = ["N0", "N3"]
CELLS = ["W-def", "W-min", "S-def", "S-min"]
EST = {"W": 0.006, "S-def": 0.004, "judge": 0.008}

sys.path.insert(0, str(ROOT / "scripts"))
from prompts import INTERPRETER_SYSTEM, TRANSLATE_USER_TEMPLATE  # noqa: E402


def files() -> list[str]:
    return sorted(Path(f).stem for f in glob.glob(str(ROOT / "results" / "raw" / "Cplus" / "*__N*.json"))
                  if Path(f).stem.split("__")[1] in CONDS)


def body(text: str, thinking: str) -> dict:
    gen: dict = {"temperature": 0.2}
    if thinking == "min":
        gen["thinkingConfig"] = {"thinkingLevel": "minimal"}  # = 產品
    # "def" = 不送 thinkingConfig(= exp1 translate_c.py)
    return {"systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
            "contents": [{"parts": [{"text": TRANSLATE_USER_TEMPLATE.format(transcript=text)}]}],
            "generationConfig": gen}


def sentences_of(f: str) -> list[dict]:
    return sorted([it for it in v13.main_items() if it["file"] == f], key=lambda it: it["i"])


# ── 翻譯 ─────────────────────────────────────────────────────────

def stage_translate():
    todo = []
    for f in files():
        transcript = json.loads((ROOT / "results" / "raw" / "Cplus" / f"{f}.json").read_text())["transcript"]
        for th in ("def", "min"):
            out = RAW / "translate" / f"{f}__W-{th}.json"
            if not out.exists():
                todo.append(("W", f, None, transcript, th, out))
        for it in sentences_of(f):
            out = RAW / "translate_sdef" / f"{v13.safe(it['id'])}.json"
            if not out.exists():
                todo.append(("S-def", f, it, it["text"], "def", out))
    (RAW / "translate").mkdir(parents=True, exist_ok=True)
    (RAW / "translate_sdef").mkdir(parents=True, exist_ok=True)
    random.Random(14).shuffle(todo)  # 打散:網路漂移不集中在某一格
    est = sum(EST["W"] if t[0] == "W" else EST["S-def"] for t in todo)
    print(f"translate:{len(todo)} 次待跑,估 ${est:.2f};帳本 ${v13.ledger()['spent_usd']:.3f} / ${v13.BUDGET_USD}")
    if DRY_RUN or v13.ledger()["spent_usd"] + est > v13.BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for n, (kind, f, it, text, th, out) in enumerate(todo, 1):
        if MAX_CALLS and n > MAX_CALLS:
            break
        resp, dt = v13.call(v13.TRANSLATOR, body(text, th))
        usd = v13.charge(v13.TRANSLATOR, resp)
        rec = {"kind": kind, "file": f, "thinking": th, "text": text, "zh": v13.text_of(resp),
               "latency_s": round(dt, 3), "retries": resp["_retries"], "usage": resp.get("usageMetadata"),
               "modelVersion": resp.get("modelVersion"), "usd": usd}
        if it:
            rec.update({"id": it["id"], "i": it["i"]})
        out.write_text(json.dumps(rec, ensure_ascii=False))
        if n <= 2 or n % 40 == 0:
            u = rec["usage"] or {}
            print(f"  {n}/{len(todo)} {kind}-{th} in {u.get('promptTokenCount')} out {u.get('candidatesTokenCount')} "
                  f"thoughts {u.get('thoughtsTokenCount', 0)} ${usd:.5f} {dt:.2f}s 帳本 ${v13.ledger()['spent_usd']:.3f}", flush=True)


def doc_zh(f: str, cell: str) -> str | None:
    if cell.startswith("W"):
        p = RAW / "translate" / f"{f}__{cell}.json"
        return json.loads(p.read_text())["zh"] if p.exists() else None
    trs = []
    for it in sentences_of(f):
        p = (RAW / "translate_sdef" / f"{v13.safe(it['id'])}.json") if cell == "S-def" else \
            (v13.RAW / "translate" / f"{v13.safe(it['id'])}__B.json")
        if not p.exists():
            return None
        trs.append(json.loads(p.read_text())["zh"])
    return "".join(trs)


# ── 評審 ─────────────────────────────────────────────────────────

def stage_judge():
    from judge import PROMPT

    outdir = RAW / "judge_doc"
    outdir.mkdir(parents=True, exist_ok=True)
    todo = []
    for f in files():
        ref = (ROOT / "corpus" / "reference" / f"{f.split('__')[0]}.txt").read_text()
        for cell in ("W-def", "W-min", "S-def"):  # S-min 沿用 v13 judge_doc 的 B
            zh = doc_zh(f, cell)
            if zh is None:
                print(f"  缺譯文 {f} {cell}", file=sys.stderr)
                continue
            for m in v13.DOC_JUDGES:
                out = outdir / f"{f}__{cell}__{m}.json"
                if not out.exists():
                    todo.append((f, cell, m, ref, zh, out))
    est = len(todo) * EST["judge"]
    print(f"judge:{len(todo)} 次待評,估 ${est:.2f};帳本 ${v13.ledger()['spent_usd']:.3f}")
    if DRY_RUN or v13.ledger()["spent_usd"] + est > v13.BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for f, cell, m, ref, zh, out in todo:
        resp, _ = v13.call(m, {"contents": [{"parts": [{"text": PROMPT.format(ref=ref, zh=zh)}]}],
                               "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        v13.charge(m, resp)
        raw = v13.text_of(resp)
        try:
            j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            rec = {"file": f, "cell": cell, "judge": m, "adequacy": int(j["adequacy"]), "tw_locale": int(j["tw_locale"]),
                   "reason": j.get("reason"), "modelVersion": resp.get("modelVersion")}
        except Exception as e:  # noqa: BLE001
            rec = {"file": f, "cell": cell, "judge": m, "parse_error": str(e), "raw": raw}
        out.write_text(json.dumps(rec, ensure_ascii=False))


# ── 分析(handoff-v14 §3)────────────────────────────────────────

def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]


def stage_analyze():
    recs = []
    for f in glob.glob(str(RAW / "judge_doc" / "*.json")):
        recs.append(json.loads(Path(f).read_text()))
    for f in glob.glob(str(v13.RAW / "judge_doc" / "*__B__*.json")):  # S-min = v13 B
        d = json.loads(Path(f).read_text())
        d["cell"] = "S-min"
        recs.append(d)
    for f in glob.glob(str(v13.RAW / "judge_doc_exp1" / "*.json")):  # 8 月 exp1 譯文、v13 同批評審
        d = json.loads(Path(f).read_text())
        d["cell"] = "exp1-aug"
        recs.append(d)
    errors = [(d["file"], d["cell"], d["judge"]) for d in recs if "adequacy" not in d]
    recs = [d for d in recs if "adequacy" in d and d["file"] in files()]
    # 檔 × 格:三評審平均
    cellmean: dict[tuple[str, str], float] = {}
    for f in files():
        for c in CELLS + ["exp1-aug"]:
            v = [d["adequacy"] for d in recs if d["file"] == f and d["cell"] == c]
            if v:
                cellmean[(f, c)] = statistics.mean(v)
    F = [f for f in files() if all((f, c) in cellmean for c in CELLS)]

    def effects(fs):
        m = {c: statistics.mean(cellmean[(f, c)] for f in fs) for c in CELLS}
        return {
            "cells": m,
            "granularity": (m["W-def"] + m["W-min"]) / 2 - (m["S-def"] + m["S-min"]) / 2,
            "thinking": (m["W-def"] + m["S-def"]) / 2 - (m["W-min"] + m["S-min"]) / 2,
            "interaction": (m["W-def"] - m["W-min"]) - (m["S-def"] - m["S-min"]),
            "total_gap": m["W-def"] - m["S-min"],
        }

    point = effects(F)
    rnd = random.Random(2026)
    boots = {k: [] for k in ("granularity", "thinking", "interaction", "total_gap")}
    for _ in range(10000):
        e = effects([F[rnd.randrange(len(F))] for _ in F])
        for k in boots:
            boots[k].append(e[k])
    res = {
        "handoff": "handoff-v14.md", "files": len(F), "judges": v13.DOC_JUDGES, "parse_errors": errors,
        "cells": {c: round(v, 3) for c, v in point["cells"].items()},
        "by_condition": {
            cond: {c: round(statistics.mean(cellmean[(f, c)] for f in F if f.endswith(cond)), 3)
                   for c in CELLS + ["exp1-aug"] if all((f, c) in cellmean for f in F if f.endswith(cond))}
            for cond in CONDS
        },
        "effects": {k: {"point": round(point[k], 3), "ci95": [round(q(b, .025), 3), round(q(b, .975), 3)]}
                    for k, b in boots.items()},
    }
    g, t, tot = point["granularity"], point["thinking"], point["total_gap"]
    res["share_of_gap"] = {"granularity": round(g / tot, 2) if tot else None, "thinking": round(t / tot, 2) if tot else None}
    # R0 漂移:W-def(今天)vs exp1 C+ 8 月譯文
    if all((f, "exp1-aug") in cellmean for f in F):
        a = statistics.mean(cellmean[(f, "exp1-aug")] for f in F)
        res["R0_drift"] = {"W-def_today": round(point["cells"]["W-def"], 3), "exp1_aug": round(a, 3),
                           "diff": round(point["cells"]["W-def"] - a, 3)}
    # 逐句成本與延遲:S-def vs S-min
    sdef = [json.loads(Path(p).read_text()) for p in glob.glob(str(RAW / "translate_sdef" / "*.json"))]
    ids = {r["id"] for r in sdef}
    smin = [json.loads(Path(p).read_text()) for p in glob.glob(str(v13.RAW / "translate" / "*__B.json"))]
    smin = [r for r in smin if r["id"] in ids]
    tok = lambda rs, k: statistics.mean((r["usage"] or {}).get(k, 0) for r in rs)
    res["per_sentence_cost"] = {
        c: {"n": len(rs), "latency_p50": round(q([r["latency_s"] for r in rs], .5), 3),
            "latency_p90": round(q([r["latency_s"] for r in rs], .9), 3),
            "thoughts_mean": round(tok(rs, "thoughtsTokenCount"), 1),
            "output_mean": round(tok(rs, "candidatesTokenCount"), 1),
            "usd_per_sentence": round(statistics.mean(r["usd"] for r in rs), 6)}
        for c, rs in (("S-def", sdef), ("S-min", smin)) if rs
    }
    res["ledger"] = v13.ledger()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    {"translate": stage_translate, "judge": stage_judge, "analyze": stage_analyze}[sys.argv[1]]()
