#!/usr/bin/env python3
"""handoff-v15:產品逐句翻譯換到 gemini-3.6-flash 後重量(對照 v13 的 3.5 輸出)。

判讀規則在 handoff-v15.md §4(先 commit 才跑)。呼叫、帳本、句子集沿用 v13 的實作;評審 PROMPT 沿用 exp1 judge.py。

  python3 scripts/model36_v15.py translate   # S-3.6:408 句,併發 1、順序打散
  python3 scripts/model36_v15.py judge       # 12 檔 × {S-3.5, S-3.6} × 3 評審 × 2 次
  python3 scripts/model36_v15.py analyze     # → results/model36_v15.json

保險絲 BUDGET_USD(預設 5.00),帳本 results/raw/_v15_ledger.json。MAX_CALLS=N 只打前 N 筆(對價用)。
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
sys.path.insert(0, str(ROOT / "scripts"))
_spec = importlib.util.spec_from_file_location("v13", ROOT / "scripts" / "context_translate_v13.py")
v13 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v13)

from prompts import INTERPRETER_SYSTEM, TRANSLATE_USER_TEMPLATE  # noqa: E402
from score import TW_BAD, SIMPLIFIED_RE  # noqa: E402
from scan_style import KANA, LATIN, classify_latin  # noqa: E402

# 帳本與保險絲換成 v15 自己的;牌價補上這輪的型號(handoff-v15 §5,3.6 用原價不算促銷)
v13.LEDGER = ROOT / "results" / "raw" / "_v15_ledger.json"
v13.BUDGET_USD = float(os.environ.get("BUDGET_USD", "5.0"))
v13.PRICE.update({
    "gemini-3.6-flash": (1.50, 7.50),
    "gemini-3.1-pro-preview": (2.00, 12.00),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3-flash-preview": (0.50, 3.00),
})
DRY_RUN = os.environ.get("DRY_RUN") == "1"
MAX_CALLS = int(os.environ.get("MAX_CALLS", "0"))

TRANSLATOR = "gemini-3.6-flash"
# 寫死型號 ID,不用 *-latest 別名;拿掉 3.6-flash(它現在就是翻譯模型)
# gemini-2.5-pro 原本是第三位,2026-10-11 實測 404「已不開放給新使用者」→ 換成 gemini-3-flash-preview(handoff-v15 §7-1)
JUDGES = ["gemini-3.1-pro-preview", "gemini-3.5-flash-lite", "gemini-3-flash-preview"]
REPEATS = 2
CONDS = ["N0", "N3"]
RAW = ROOT / "results" / "raw" / "v15"
OUT = ROOT / "results" / "model36_v15.json"
EST = {"translate": 0.0004, "judge": 0.02}


def items() -> list[dict]:
    return v13.main_items()


def tr_path(arm: str, it: dict) -> Path:
    if arm == "S-3.5":
        return v13.RAW / "translate" / f"{v13.safe(it['id'])}__B.json"
    return RAW / "translate" / f"{v13.safe(it['id'])}.json"


def load_tr(arm: str, it: dict) -> dict | None:
    p = tr_path(arm, it)
    return json.loads(p.read_text()) if p.exists() else None


# ── 翻譯(= 改完之後的產品呼叫)──────────────────────────────────────

def body(text: str) -> dict:
    return {
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "contents": [{"parts": [{"text": TRANSLATE_USER_TEMPLATE.format(transcript=text)}]}],
        # 不送 temperature(產品 2026-10-11 起同此);thinking minimal(鐵律 4)
        "generationConfig": {"thinkingConfig": {"thinkingLevel": "minimal"}},
    }


def stage_translate():
    (RAW / "translate").mkdir(parents=True, exist_ok=True)
    todo = [it for it in items() if not tr_path("S-3.6", it).exists()]
    random.Random(15).shuffle(todo)
    est = len(todo) * EST["translate"]
    print(f"translate:{len(todo)} 次待跑,估 ${est:.2f};帳本 ${v13.ledger()['spent_usd']:.3f} / ${v13.BUDGET_USD}")
    if DRY_RUN or v13.ledger()["spent_usd"] + est > v13.BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for n, it in enumerate(todo, 1):
        if MAX_CALLS and n > MAX_CALLS:
            break
        resp, dt = v13.call(TRANSLATOR, body(it["text"]))
        usd = v13.charge(TRANSLATOR, resp)
        rec = {"id": it["id"], "file": it["file"], "i": it["i"], "text": it["text"], "zh": v13.text_of(resp),
               "latency_s": round(dt, 3), "retries": resp["_retries"], "usage": resp.get("usageMetadata"),
               "modelVersion": resp.get("modelVersion"), "usd": usd}
        tr_path("S-3.6", it).write_text(json.dumps(rec, ensure_ascii=False))
        if n <= 2 or n % 50 == 0:
            u = rec["usage"] or {}
            print(f"  {n}/{len(todo)} {rec['modelVersion']} in {u.get('promptTokenCount')} out {u.get('candidatesTokenCount')} "
                  f"thoughts {u.get('thoughtsTokenCount', 0)} ${usd:.6f} {dt:.2f}s 帳本 ${v13.ledger()['spent_usd']:.3f}", flush=True)


# ── 評審(exp1 同 PROMPT;新評審組;每位兩次)───────────────────────────

def doc_files() -> list[str]:
    return sorted({it["file"] for it in items() if it["file"].split("__")[1] in CONDS})


def doc_zh(arm: str, f: str) -> str | None:
    sents = sorted([it for it in items() if it["file"] == f], key=lambda it: it["i"])
    trs = [load_tr(arm, it) for it in sents]
    return "".join(t["zh"] for t in trs) if all(trs) else None


def stage_judge():
    from judge import PROMPT

    outdir = RAW / "judge_doc"
    outdir.mkdir(parents=True, exist_ok=True)
    todo = []
    for f in doc_files():
        ref = (ROOT / "corpus" / "reference" / f"{f.split('__')[0]}.txt").read_text()
        for arm in ("S-3.5", "S-3.6"):
            zh = doc_zh(arm, f)
            if zh is None:
                print(f"  缺譯文 {f} {arm}", file=sys.stderr)
                continue
            for m in JUDGES:
                for r in range(1, REPEATS + 1):
                    out = outdir / f"{f}__{arm}__{m}__r{r}.json"
                    if not out.exists():
                        todo.append((f, arm, m, r, ref, zh, out))
    random.Random(1515).shuffle(todo)  # 兩個 arm 交錯送,評審的時段漂移不集中在某一邊
    est = len(todo) * EST["judge"]
    print(f"judge:{len(todo)} 次待評,估 ${est:.2f};帳本 ${v13.ledger()['spent_usd']:.3f}")
    if DRY_RUN or v13.ledger()["spent_usd"] + est > v13.BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for n, (f, arm, m, r, ref, zh, out) in enumerate(todo, 1):
        if MAX_CALLS and n > MAX_CALLS:
            break
        resp, _ = v13.call(m, {"contents": [{"parts": [{"text": PROMPT.format(ref=ref, zh=zh)}]}],
                               "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        usd = v13.charge(m, resp)
        raw = v13.text_of(resp)
        try:
            j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            rec = {"file": f, "arm": arm, "judge": m, "rep": r, "adequacy": int(j["adequacy"]),
                   "tw_locale": int(j["tw_locale"]), "reason": j.get("reason"), "modelVersion": resp.get("modelVersion"), "usd": usd}
        except Exception as e:  # noqa: BLE001
            rec = {"file": f, "arm": arm, "judge": m, "rep": r, "parse_error": str(e), "raw": raw, "usd": usd}
        out.write_text(json.dumps(rec, ensure_ascii=False))
        if n <= 2 or n % 24 == 0:
            print(f"  {n}/{len(todo)} {m} ${usd:.4f} 帳本 ${v13.ledger()['spent_usd']:.3f}", flush=True)


# ── 分析(handoff-v15 §4)──────────────────────────────────────────

def q(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]


def locale_scan(arm: str) -> dict:
    c = {"sentences": 0, "tw_bad_hits": 0, "simplified_chars": 0, "kana_residue": 0, "latin_bare": 0, "latin_gloss": 0}
    ex = []
    for it in items():
        t = load_tr(arm, it)
        if not t:
            continue
        zh = t["zh"]
        c["sentences"] += 1
        bad = sum(zh.count(w) for w in TW_BAD)
        simp = SIMPLIFIED_RE.findall(zh)
        c["tw_bad_hits"] += bad
        c["simplified_chars"] += len(simp)
        if bad or simp:
            ex.append({"id": it["id"], "zh": zh, "simplified": simp})
        c["kana_residue"] += len(KANA.findall(zh))
        for m in LATIN.finditer(zh):
            if len(m.group()) >= 2:
                c[classify_latin(zh, m)] += 1
    c["examples"] = ex[:20]
    return c


def stage_analyze():
    recs = [json.loads(Path(p).read_text()) for p in glob.glob(str(RAW / "judge_doc" / "*.json"))]
    errors = [(d["file"], d["arm"], d["judge"], d["rep"]) for d in recs if "adequacy" not in d]
    recs = [d for d in recs if "adequacy" in d]
    files = doc_files()
    cell = {(f, a): statistics.mean([d["adequacy"] for d in recs if d["file"] == f and d["arm"] == a])
            for f in files for a in ("S-3.5", "S-3.6") if any(d["file"] == f and d["arm"] == a for d in recs)}
    F = [f for f in files if (f, "S-3.5") in cell and (f, "S-3.6") in cell]
    diff = lambda fs: statistics.mean(cell[(f, "S-3.6")] - cell[(f, "S-3.5")] for f in fs)
    rnd = random.Random(2026)
    boots = [diff([F[rnd.randrange(len(F))] for _ in F]) for _ in range(10000)]
    mean_arm = lambda a, fs: round(statistics.mean(cell[(f, a)] for f in fs), 3)
    res = {
        "handoff": "handoff-v15.md", "translator": TRANSLATOR, "judges": JUDGES, "repeats": REPEATS,
        "files": len(F), "parse_errors": errors,
        "adequacy": {
            "S-3.5": mean_arm("S-3.5", F), "S-3.6": mean_arm("S-3.6", F),
            "by_condition": {c: {a: mean_arm(a, [f for f in F if f.endswith(c)]) for a in ("S-3.5", "S-3.6")} for c in CONDS},
            "by_judge": {m: {a: round(statistics.mean([d["adequacy"] for d in recs if d["judge"] == m and d["arm"] == a]), 3)
                             for a in ("S-3.5", "S-3.6")} for m in JUDGES},
            "diff": round(diff(F), 3), "diff_ci95": [round(q(boots, .025), 3), round(q(boots, .975), 3)],
        },
    }
    # 評審雜訊:同評審、同檔、同 arm 的兩次
    noise = {}
    for m in JUDGES:
        pairs = []
        for f in files:
            for a in ("S-3.5", "S-3.6"):
                rs = {d["rep"]: d["adequacy"] for d in recs if d["judge"] == m and d["file"] == f and d["arm"] == a}
                if 1 in rs and 2 in rs:
                    pairs.append(abs(rs[1] - rs[2]))
        noise[m] = {"pairs": len(pairs), "mean_abs_diff": round(statistics.mean(pairs), 3) if pairs else None,
                    "exact_agree": round(sum(1 for x in pairs if x == 0) / len(pairs), 3) if pairs else None}
    res["judge_noise"] = noise
    res["locale"] = {a: locale_scan(a) for a in ("S-3.5", "S-3.6")}
    # 延遲 / 花費
    lat = {}
    for a in ("S-3.5", "S-3.6"):
        rs = [t for t in (load_tr(a, it) for it in items()) if t]
        u = lambda k: statistics.mean((r["usage"] or {}).get(k, 0) for r in rs)
        lat[a] = {"n": len(rs), "p50": round(q([r["latency_s"] for r in rs], .5), 3), "p90": round(q([r["latency_s"] for r in rs], .9), 3),
                  "prompt": round(u("promptTokenCount"), 1), "output": round(u("candidatesTokenCount"), 1),
                  "thoughts": round(u("thoughtsTokenCount"), 1),
                  "modelVersion": sorted({r.get("modelVersion") for r in rs if r.get("modelVersion")})}
    res["latency_tokens"] = lat
    res["ledger"] = v13.ledger()
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "locale"}, ensure_ascii=False, indent=1))
    print("locale:", {a: {k: v for k, v in d.items() if k != "examples"} for a, d in res["locale"].items()})


if __name__ == "__main__":
    {"translate": stage_translate, "judge": stage_judge, "analyze": stage_analyze}[sys.argv[1]]()
