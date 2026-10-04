#!/usr/bin/env python3
"""handoff-v13:逐句翻譯帶「上一句原文」當上下文,量延遲、溢出與品質。

判讀規則寫在 handoff-v13.md §5(先 commit 才跑),這支只負責照做。

階段(依序跑,每階段都可中斷重跑,已落檔的呼叫不會再付錢):
  python3 scripts/context_translate_v13.py translate     # B / X 兩 arm 逐句翻譯,併發 1、順序隨機
  python3 scripts/context_translate_v13.py judge_pair    # 逐句兩兩盲評 + 溢出標記
  python3 scripts/context_translate_v13.py judge_doc     # 整段 adequacy(exp1 同 PROMPT、同三評審)
  python3 scripts/context_translate_v13.py analyze       # → results/context_v13.json

主集 = exp1 C+ 30 檔的 SM 即時定稿,用 analysis/segmentation_replay.py 的 new 規則(現行產品斷句)重組。
副集 = 使用者上傳的試酒紀錄(FIELD_DIR 指定的 kikemu-*.md);**原始輸出不進 repo**
(使用者的紀錄內容,只在 FIELD_OUT 指定的目錄),results 只留彙總數字。

保險絲:BUDGET_USD(預設 5.00),以 usageMetadata 實算、帳本 results/raw/_v13_ledger.json,
每筆落檔後才打下一筆。DRY_RUN=1 只印計畫與估價。
"""
from __future__ import annotations

import glob
import importlib.util
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from prompts import INTERPRETER_SYSTEM, TRANSLATE_USER_TEMPLATE  # noqa: E402

_spec = importlib.util.spec_from_file_location("seg", ROOT / "analysis" / "segmentation_replay.py")
seg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(seg)

G_KEY = os.environ["gemini_key"]
RAW = ROOT / "results" / "raw" / "v13"
LEDGER = ROOT / "results" / "raw" / "_v13_ledger.json"
OUT = ROOT / "results" / "context_v13.json"
FIELD_DIR = os.environ.get("FIELD_DIR", "")
FIELD_OUT = Path(os.environ.get("FIELD_OUT", "/nonexistent"))
BUDGET_USD = float(os.environ.get("BUDGET_USD", "5.0"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"

TRANSLATOR = "gemini-3.5-flash"  # = 產品 TRANSLATE_MODEL
PAIR_JUDGE = "gemini-3.6-flash"
DOC_JUDGES = ["gemini-3.6-flash", "gemini-pro-latest", "gemini-flash-lite-latest"]  # = exp1 judge.py
DOC_CONDS = ["N0", "N3"]  # = exp1 judge.py
# $/M tokens(in, out;thinking 以輸出價計)。官方 pricing 頁「Last updated 2026-10-01」,2026-10-04 查。
# pro-latest 是別名、對應型號未公告 → 保守用 3.1 Pro 的價;flash-lite-latest 用 3.5-flash-lite。
PRICE = {
    "gemini-3.5-flash": (1.50, 9.00),
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-pro-latest": (2.00, 12.00),
    "gemini-flash-lite-latest": (0.30, 2.50),
}
EST_USD = {"translate": 0.0008, "pair": 0.004, "doc": 0.008}  # 每次呼叫的估計,只用於開跑前的總估

FIELD_TAGS = {"20260925-1806", "20261004-2051", "20261004-2142", "20261004-2158"}

CTX_PREFIX = "直前の発話(文脈の参考のみ。この部分は翻訳しないこと):\n{prev}\n\n"


# ── 資料 ─────────────────────────────────────────────────────────

def main_items() -> list[dict]:
    items = []
    for f in sorted(glob.glob(str(ROOT / "results" / "raw" / "Cplus" / "*__N*.json"))):
        name = Path(f).stem
        d = json.load(open(f, encoding="utf-8"))
        ev = [(e["t"], e["transcript"] or "") for e in d["log"] if e.get("kind") == "AddTranscript"]
        sents = [s["text"] for s in seg.replay(ev, True, True)]
        for i, s in enumerate(sents):
            items.append({"id": f"{name}#{i:03d}", "set": "main", "file": name, "i": i,
                          "text": s, "prev": sents[i - 1] if i else None})
    return items


def field_items() -> list[dict]:
    if not FIELD_DIR:
        return []
    items = []
    for f in sorted(glob.glob(os.path.join(FIELD_DIR, "*kikemu-*.md"))):
        tag = re.search(r"kikemu-(\d{8}-\d{4})", f).group(1)
        if tag not in FIELD_TAGS:  # handoff-v13 §3:只收那 4 份試酒紀錄(導覽模式)
            continue
        md = open(f, encoding="utf-8").read()
        sents = [l.split("|")[2].strip() for l in md.splitlines() if re.match(r"\| \d+ \|", l)]
        for i, s in enumerate(sents):
            items.append({"id": f"field-{tag}#{i:03d}", "set": "field", "file": tag, "i": i,
                          "text": s, "prev": sents[i - 1] if i else None})
    return items


def raw_dir(item: dict, stage: str) -> Path:
    base = FIELD_OUT if item["set"] == "field" else RAW
    p = base / stage
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe(s: str) -> str:
    return s.replace("#", "_")


# ── 帳本與呼叫 ────────────────────────────────────────────────────

def ledger() -> dict:
    return json.loads(LEDGER.read_text()) if LEDGER.exists() else {"spent_usd": 0.0, "calls": 0}


def charge(model: str, resp: dict) -> float:
    u = resp.get("usageMetadata") or {}
    pin, pout = PRICE[model]
    usd = u.get("promptTokenCount", 0) / 1e6 * pin + (
        u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)) / 1e6 * pout
    L = ledger()
    L["spent_usd"] = round(L["spent_usd"] + usd, 6)
    L["calls"] += 1
    LEDGER.write_text(json.dumps(L))
    return usd


def call(model: str, body: dict) -> tuple[dict, float]:
    """回傳 (response, 成功那一次的牆鐘秒數)。429 / 5xx 退避重試,其他 4xx 直接失敗。"""
    if ledger()["spent_usd"] >= BUDGET_USD:
        sys.exit(f"ABORT: 帳本 ${ledger()['spent_usd']:.3f} ≥ BUDGET_USD ${BUDGET_USD}(已落檔的保留,重跑可續)")
    last = ""
    for attempt, wait in enumerate((0, 4, 15, 40)):
        if wait:
            time.sleep(wait)
        t0 = time.monotonic()
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
                json=body, timeout=120)
            dt = time.monotonic() - t0
            if r.ok:
                resp = r.json()
                resp["_retries"] = attempt
                return resp, dt
            if r.status_code < 500 and r.status_code != 429:
                raise SystemExit(f"{model} HTTP {r.status_code}: {r.text[:300]}")
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)[:120]
    raise RuntimeError(f"{model} 重試用完:{last}")


def text_of(resp: dict) -> str:
    parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()


# ── 階段 1:翻譯 ──────────────────────────────────────────────────

def translate_body(item: dict, arm: str) -> dict:
    user = TRANSLATE_USER_TEMPLATE.format(transcript=item["text"])
    if arm == "X" and item["prev"]:
        user = CTX_PREFIX.format(prev=item["prev"]) + user
    return {
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "contents": [{"parts": [{"text": user}]}],
        # = 產品 generate():temperature 0.2 + thinkingLevel minimal(鐵律 4)
        "generationConfig": {"temperature": 0.2, "thinkingConfig": {"thinkingLevel": "minimal"}},
    }


def stage_translate(items: list[dict]):
    rnd = random.Random(13)
    order = items[:]
    rnd.shuffle(order)  # 句子順序打散:網路漂移不會集中在某一檔
    todo = []
    for it in order:
        arms = ["B", "X"]
        rnd.shuffle(arms)  # 同一句兩 arm 相鄰、先後隨機:成對差不受先後影響
        todo += [(it, a) for a in arms]
    pending = [(it, a) for it, a in todo if not (raw_dir(it, "translate") / f"{safe(it['id'])}__{a}.json").exists()]
    est = len(pending) * EST_USD["translate"]
    print(f"translate:{len(todo)} 次,待跑 {len(pending)},估 ${est:.2f};帳本 ${ledger()['spent_usd']:.3f} / ${BUDGET_USD}")
    if DRY_RUN or ledger()["spent_usd"] + est > BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    max_calls = int(os.environ.get("MAX_CALLS", "0"))  # >0:只打前 N 筆(對價用)
    for n, (it, arm) in enumerate(pending, 1):
        if max_calls and n > max_calls:
            break
        body = translate_body(it, arm)
        resp, dt = call(TRANSLATOR, body)
        usd = charge(TRANSLATOR, resp)
        rec = {"id": it["id"], "arm": arm, "set": it["set"], "file": it["file"], "i": it["i"],
               "text": it["text"], "prev": it["prev"], "user": body["contents"][0]["parts"][0]["text"],
               "zh": text_of(resp), "latency_s": round(dt, 3), "retries": resp["_retries"],
               "usage": resp.get("usageMetadata"), "modelVersion": resp.get("modelVersion"), "usd": usd}
        (raw_dir(it, "translate") / f"{safe(it['id'])}__{arm}.json").write_text(json.dumps(rec, ensure_ascii=False))
        if n == 1:
            u = rec["usage"] or {}
            print(f"  第一筆對價:in {u.get('promptTokenCount')} out {u.get('candidatesTokenCount')} "
                  f"thoughts {u.get('thoughtsTokenCount', 0)} → ${usd:.6f}(估 ${EST_USD['translate']})")
        if n % 50 == 0:
            print(f"  {n}/{len(pending)} 帳本 ${ledger()['spent_usd']:.3f}", flush=True)


def load_tr(item: dict, arm: str) -> dict | None:
    f = raw_dir(item, "translate") / f"{safe(item['id'])}__{arm}.json"
    return json.loads(f.read_text()) if f.exists() else None


# ── 階段 2:逐句兩兩盲評 ──────────────────────────────────────────

PAIR_PROMPT = """你是翻譯品質評審。以下是日語導覽解說中連續的兩句(語音辨識結果,可能有錯字),以及「第二句」的兩份台灣繁體中文譯文。
只評第二句的翻譯;上一句只是讓你理解語境。

請判斷:
1. better:哪一份譯文在語境下較正確、完整地傳達第二句的意思?回答 "1"、"2" 或 "tie"(差不多就選 tie)
2. leak1 / leak2:該份譯文是否包含第二句沒有、而是來自上一句的內容(把上一句也翻進去了)?true / false

只輸出 JSON:{{"better": "1|2|tie", "leak1": true|false, "leak2": true|false, "reason": "一句話"}}

## 上一句
{prev}

## 第二句
{text}

## 譯文 1
{zh1}

## 譯文 2
{zh2}
"""


def stage_judge_pair(items: list[dict]):
    cand = [it for it in items if it["prev"]]
    todo = []
    for it in cand:
        b, x = load_tr(it, "B"), load_tr(it, "X")
        if not (b and x):
            continue
        if not (raw_dir(it, "judge_pair") / f"{safe(it['id'])}.json").exists():
            todo.append((it, b, x))
    est = sum(1 for _, b, x in todo if b["zh"] != x["zh"]) * EST_USD["pair"]
    print(f"judge_pair:{len(todo)} 句待評(譯文完全相同的直接記平手、不呼叫),估 ${est:.2f};帳本 ${ledger()['spent_usd']:.3f}")
    if DRY_RUN or ledger()["spent_usd"] + est > BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for it, b, x in todo:
        out = raw_dir(it, "judge_pair") / f"{safe(it['id'])}.json"
        if b["zh"] == x["zh"]:
            out.write_text(json.dumps({"id": it["id"], "identical": True, "winner": "tie", "leakB": False, "leakX": False}))
            continue
        x_first = random.Random(it["id"]).random() < 0.5  # 每句固定的隨機位置
        z1, z2 = (x["zh"], b["zh"]) if x_first else (b["zh"], x["zh"])
        body = {"contents": [{"parts": [{"text": PAIR_PROMPT.format(prev=it["prev"], text=it["text"], zh1=z1, zh2=z2)}]}],
                "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}}
        resp, _ = call(PAIR_JUDGE, body)
        charge(PAIR_JUDGE, resp)
        raw = text_of(resp)
        try:
            j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            better = str(j["better"]).strip()
            winner = {"1": "X" if x_first else "B", "2": "B" if x_first else "X"}.get(better, "tie")
            leak1, leak2 = bool(j.get("leak1")), bool(j.get("leak2"))
            leakX, leakB = (leak1, leak2) if x_first else (leak2, leak1)
            rec = {"id": it["id"], "identical": False, "x_first": x_first, "winner": winner,
                   "leakB": leakB, "leakX": leakX, "reason": j.get("reason"), "raw": raw}
        except Exception as e:  # noqa: BLE001
            rec = {"id": it["id"], "parse_error": str(e), "raw": raw}
        out.write_text(json.dumps(rec, ensure_ascii=False))


# ── 階段 3:整段 adequacy(exp1 同量尺)──────────────────────────────

def stage_judge_doc(items: list[dict]):
    sys.path.insert(0, str(ROOT / "scripts"))
    from judge import PROMPT  # exp1 的同一個 PROMPT

    files = sorted({it["file"] for it in items if it["set"] == "main" and it["file"].split("__")[1] in DOC_CONDS})
    todo = []
    for f in files:
        sents = sorted([it for it in items if it["file"] == f], key=lambda it: it["i"])
        ref = (ROOT / "corpus" / "reference" / f"{f.split('__')[0]}.txt").read_text()
        for arm in ("B", "X"):
            trs = [load_tr(it, arm) for it in sents]
            if not all(trs):
                continue
            zh = "".join(t["zh"] for t in trs)
            for m in DOC_JUDGES:
                out = RAW / "judge_doc" / f"{f}__{arm}__{m}.json"
                if not out.exists():
                    todo.append((f, arm, m, ref, zh, out))
    (RAW / "judge_doc").mkdir(parents=True, exist_ok=True)
    est = len(todo) * EST_USD["doc"]
    print(f"judge_doc:{len(todo)} 次待評,估 ${est:.2f};帳本 ${ledger()['spent_usd']:.3f}")
    if DRY_RUN or ledger()["spent_usd"] + est > BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for f, arm, m, ref, zh, out in todo:
        gen = {"temperature": 0, "responseMimeType": "application/json"}
        resp, _ = call(m, {"contents": [{"parts": [{"text": PROMPT.format(ref=ref, zh=zh)}]}], "generationConfig": gen})
        charge(m, resp)
        raw = text_of(resp)
        try:
            j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            rec = {"file": f, "arm": arm, "judge": m, "adequacy": int(j["adequacy"]),
                   "tw_locale": int(j["tw_locale"]), "reason": j.get("reason"), "modelVersion": resp.get("modelVersion")}
        except Exception as e:  # noqa: BLE001
            rec = {"file": f, "arm": arm, "judge": m, "parse_error": str(e), "raw": raw}
        out.write_text(json.dumps(rec, ensure_ascii=False))


# ── 附加:exp1 C+ 整段譯文用「今天這組評審」重評(偏離紀錄 §7:非事先登記)──────────
# 為什麼:B(產品逐句)N0 整段 4.06,exp1 C+(整段一次翻)當時 4.71。但評審是 *-latest 別名,
# 8 月到今天可能已換型號,跨時間比不乾淨。同一批評審、同一天重評 exp1 的譯文才能比。

def stage_judge_exp1(items: list[dict]):
    from judge import PROMPT

    files = sorted({it["file"] for it in items if it["set"] == "main" and it["file"].split("__")[1] in DOC_CONDS})
    outdir = RAW / "judge_doc_exp1"
    outdir.mkdir(parents=True, exist_ok=True)
    todo = []
    for f in files:
        zh = json.loads((ROOT / "results" / "raw" / "Cplus_translate" / f"{f}.json").read_text())["translation"]
        ref = (ROOT / "corpus" / "reference" / f"{f.split('__')[0]}.txt").read_text()
        for m in DOC_JUDGES:
            out = outdir / f"{f}__exp1C+__{m}.json"
            if not out.exists():
                todo.append((f, m, ref, zh, out))
    est = len(todo) * EST_USD["doc"]
    print(f"judge_exp1:{len(todo)} 次待評,估 ${est:.2f};帳本 ${ledger()['spent_usd']:.3f}")
    if DRY_RUN or ledger()["spent_usd"] + est > BUDGET_USD:
        sys.exit(0 if DRY_RUN else "ABORT:估價超過保險絲")
    for f, m, ref, zh, out in todo:
        resp, _ = call(m, {"contents": [{"parts": [{"text": PROMPT.format(ref=ref, zh=zh)}]}],
                           "generationConfig": {"temperature": 0, "responseMimeType": "application/json"}})
        charge(m, resp)
        raw = text_of(resp)
        try:
            j = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
            rec = {"file": f, "arm": "exp1C+", "judge": m, "adequacy": int(j["adequacy"]),
                   "tw_locale": int(j["tw_locale"]), "reason": j.get("reason"), "modelVersion": resp.get("modelVersion")}
        except Exception as e:  # noqa: BLE001
            rec = {"file": f, "arm": "exp1C+", "judge": m, "parse_error": str(e), "raw": raw}
        out.write_text(json.dumps(rec, ensure_ascii=False))


# ── 階段 4:分析(handoff-v13 §5)──────────────────────────────────

def q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))]


def stage_analyze(items: list[dict]):
    res: dict = {"handoff": "handoff-v13.md", "translator": TRANSLATOR, "pair_judge": PAIR_JUDGE,
                 "doc_judges": DOC_JUDGES, "ledger": ledger()}
    for set_name in ("main", "field"):
        S = [it for it in items if it["set"] == set_name]
        if not S:
            continue
        pairs = [(it, load_tr(it, "B"), load_tr(it, "X")) for it in S]
        pairs = [(it, b, x) for it, b, x in pairs if b and x]
        lb = [b["latency_s"] for _, b, _ in pairs]
        lx = [x["latency_s"] for _, _, x in pairs]
        d = [x["latency_s"] - b["latency_s"] for _, b, x in pairs]
        tok = lambda r, k: (r["usage"] or {}).get(k, 0)
        r: dict = {
            "sentences": len(pairs),
            "latency": {
                "B_p50": round(q(lb, .5), 3), "B_p90": round(q(lb, .9), 3),
                "X_p50": round(q(lx, .5), 3), "X_p90": round(q(lx, .9), 3),
                "diff_p50": round(q(d, .5), 3), "diff_p90": round(q(d, .9), 3),
                "p90_diff_of_quantiles": round(q(lx, .9) - q(lb, .9), 3),
                "retried_calls": sum(1 for _, b, x in pairs for t in (b, x) if t["retries"]),
            },
            "tokens_mean": {
                arm: {k: round(statistics.mean(tok(t, k) for t in col), 1)
                      for k in ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount")}
                for arm, col in (("B", [b for _, b, _ in pairs]), ("X", [x for _, _, x in pairs]))
            },
            "output_chars_mean": {"B": round(statistics.mean(len(b["zh"]) for _, b, _ in pairs), 1),
                                  "X": round(statistics.mean(len(x["zh"]) for _, _, x in pairs), 1)},
        }
        J = []
        for it, b, x in pairs:
            if not it["prev"]:
                continue
            f = raw_dir(it, "judge_pair") / f"{safe(it['id'])}.json"
            if f.exists():
                j = json.loads(f.read_text())
                if "winner" in j:
                    J.append((it, j, b, x))
        if J:
            n = len(J)
            win = sum(1 for _, j, _, _ in J if j["winner"] == "X")
            loss = sum(1 for _, j, _, _ in J if j["winner"] == "B")
            # 以檔為群的 bootstrap
            by: dict[str, list[int]] = {}
            for it, j, _, _ in J:
                by.setdefault(it["file"], []).append(1 if j["winner"] == "X" else -1 if j["winner"] == "B" else 0)
            keys = sorted(by)
            rnd = random.Random(2026)
            boots = []
            for _ in range(10000):
                pick = [by[keys[rnd.randrange(len(keys))]] for _ in keys]
                tot = sum(len(p) for p in pick)
                boots.append(sum(sum(p) for p in pick) / tot)
            r["pairwise"] = {
                "compared": n, "identical_outputs": sum(1 for _, j, _, _ in J if j.get("identical")),
                "X_wins": win, "B_wins": loss, "ties": n - win - loss,
                "net": round((win - loss) / n, 4),
                "net_ci95": [round(q(boots, .025), 4), round(q(boots, .975), 4)],
                "clusters": len(keys),
                "leak_flag_X": sum(1 for _, j, _, _ in J if j.get("leakX")),
                "leak_flag_B": sum(1 for _, j, _, _ in J if j.get("leakB")),
                "leak_flag_X_ids": [it["id"] for it, j, _, _ in J if j.get("leakX")],
                "leak_flag_B_ids": [it["id"] for it, j, _, _ in J if j.get("leakB")],
            }
        res[set_name] = r
    # 整段
    docs = [json.loads(Path(f).read_text()) for f in glob.glob(str(RAW / "judge_doc" / "*.json"))]
    docs = [d for d in docs if "adequacy" in d]
    if docs:
        mean = lambda arm, k: round(statistics.mean(d[k] for d in docs if d["arm"] == arm), 3)
        res["doc_adequacy"] = {
            "n_per_arm": sum(1 for d in docs if d["arm"] == "B"),
            "B": mean("B", "adequacy"), "X": mean("X", "adequacy"),
            "diff": round(mean("X", "adequacy") - mean("B", "adequacy"), 3),
            "tw_locale_B": mean("B", "tw_locale"), "tw_locale_X": mean("X", "tw_locale"),
            "by_judge": {m: {arm: round(statistics.mean(d["adequacy"] for d in docs if d["judge"] == m and d["arm"] == arm), 3)
                             for arm in ("B", "X")} for m in DOC_JUDGES},
        }
        e1 = [json.loads(Path(f).read_text()) for f in glob.glob(str(RAW / "judge_doc_exp1" / "*.json"))]
        e1 = [d for d in e1 + docs if "adequacy" in d]
        res["doc_adequacy"]["by_condition"] = {
            c: {arm: round(statistics.mean(d["adequacy"] for d in e1 if d["arm"] == arm and d["file"].endswith(c)), 3)
                for arm in ("B", "X", "exp1C+") if any(d["arm"] == arm and d["file"].endswith(c) for d in e1)}
            for c in DOC_CONDS
        }
        res["doc_adequacy"]["parse_errors"] = [
            (d["file"], d["arm"], d["judge"]) for d in
            [json.loads(Path(f).read_text()) for f in glob.glob(str(RAW / "judge_doc*" / "*.json"))] if "adequacy" not in d]
    OUT.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps({k: v for k, v in res.items() if k != "ledger"}, ensure_ascii=False, indent=1)[:4000])
    print("帳本:", res["ledger"])


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    items = main_items() + field_items()
    print(f"主集 {sum(1 for i in items if i['set'] == 'main')} 句;副集 {sum(1 for i in items if i['set'] == 'field')} 句")
    {"translate": stage_translate, "judge_pair": stage_judge_pair,
     "judge_doc": stage_judge_doc, "judge_exp1": stage_judge_exp1, "analyze": stage_analyze}[stage](items)


if __name__ == "__main__":
    main()
