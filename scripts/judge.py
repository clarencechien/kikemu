#!/usr/bin/env python3
"""Adequacy judging panel — blind, temperature 0.

Intended design was manemu's 5-vendor panel via OpenRouter; the OpenRouter
and OpenAI keys in this environment are invalid (401), so the panel is
3 model families served by the Gemini API (documented deviation):
gemini-3.6-flash, gemini-3-pro-preview, gemma-4-31b-it.

Each judge sees the VERIFIED Japanese reference (not any ASR output) and one
candidate zh-TW translation, without knowing which arm produced it.
Scores 1-5 adequacy + 1-5 Taiwan-locale fit. Raw responses stored.
Conditions judged: N0 (clean) and N3 (peak crowd) by default.

Spend control (a full run is ~108 paid calls, one of them on a pro-tier model
whose thinking runs ~18x output — that is real money, unattended):
- DRY_RUN=1 prints the call plan and the estimated cost, then exits.
- BUDGET_USD caps the run; the estimate is checked before the first call and
  the running total after each one, so it cannot be walked past.
- Judging is a reasoning-shaped task, so thinking stays ON here (unlike the
  translate hop) — but it is measured and reported, not ignored.
- Every response is checkpointed to results/raw/judge/ before the next paid
  call, so a kill costs one step.
- Transient failures retry with backoff; a judge that fails MAX_TRIES in a row
  is skipped loudly rather than silently dropped.
"""
import json
import os
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
REF = ROOT / "corpus" / "reference"
G_KEY = os.environ["gemini_key"]

# gemini-3-pro-preview 404s and gemma-4 emits no JSON with this key/prompt;
# final panel = three distinct served models (one vendor, documented deviation).
JUDGES = [
    "gemini-3.6-flash",
    "gemini-pro-latest",
    "gemini-flash-lite-latest",
]
CONDS = ["N0", "N3"]
ARMS = ["A", "C", "Cplus"]

MAX_TRIES = 3            # per call; then give up loudly
RETRY_SLEEP = (2, 8, 20) # seconds, exponential-ish
BUDGET_USD = float(os.environ.get("BUDGET_USD", "3.0"))
DRY_RUN = os.environ.get("DRY_RUN") == "1"
# Rough per-call cost for the estimate: measured medians from an earlier run
# (pro-tier judges carry ~18x thinking). Verified totals are printed at the end.
EST_USD_PER_CALL = {"gemini-pro-latest": 0.020, "gemini-3.6-flash": 0.004,
                    "gemini-flash-lite-latest": 0.001}
PRICE = {  # $/M tokens (in, out) — official pricing page, checked 2026-08-14
    "gemini-3.6-flash": (0.75, 3.75),
    "gemini-pro-latest": (1.25, 10.00),
    "gemini-flash-lite-latest": (0.30, 2.50),
}


def call_usd(model: str, resp: dict) -> float:
    u = resp.get("usageMetadata") or {}
    pin, pout = PRICE.get(model, (1.50, 9.00))
    # thinking bills at the output rate
    out = u.get("candidatesTokenCount", 0) + u.get("thoughtsTokenCount", 0)
    return u.get("promptTokenCount", 0) / 1e6 * pin + out / 1e6 * pout

PROMPT = """你是翻譯品質評審。以下是一段日語導覽解說的正確原文,以及一份翻譯成台灣繁體中文的候選譯文(翻譯來源是語音辨識結果,可能含辨識錯誤)。

請就兩個面向各給 1-5 分(整數):
1. adequacy:譯文傳達原文資訊的完整與正確程度(5=完整正確,1=大量錯漏)
2. tw_locale:譯文是否符合台灣用語習慣、使用正體中文(5=完全道地,1=明顯中國大陸用語或簡體字)

只輸出 JSON:{{"adequacy": n, "tw_locale": n, "reason": "一句話"}}

## 日語原文
{ref}

## 候選譯文
{zh}
"""


def judge(model: str, ref: str, zh: str) -> dict:
    gen = {"temperature": 0}
    if not model.startswith("gemma"):
        gen["responseMimeType"] = "application/json"
    body = {
        "contents": [{"parts": [{"text": PROMPT.format(ref=ref, zh=zh)}]}],
        "generationConfig": gen,
    }
    last = None
    for attempt in range(MAX_TRIES):
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": G_KEY, "Content-Type": "application/json"},
                json=body,
                timeout=180,
            )
            # 4xx other than 429 will not fix themselves — fail fast, do not burn retries
            if r.status_code < 500 and r.status_code != 429:
                r.raise_for_status()
                return r.json()
            last = f"HTTP {r.status_code}: {r.text[:120]}"
        except Exception as e:  # noqa: BLE001 — network/timeouts are all retryable here
            last = str(e)
        if attempt < MAX_TRIES - 1:
            time.sleep(RETRY_SLEEP[attempt])
    raise RuntimeError(f"{MAX_TRIES} attempts failed: {last}")


def get_zh(arm: str, seg: str, cond: str) -> str | None:
    if arm == "A":
        f = RES / "raw" / "A" / f"{seg}__{cond}.json"
        return json.loads(f.read_text())["translation"] if f.exists() else None
    f = RES / "raw" / f"{arm}_translate" / f"{seg}__{cond}.json"
    return json.loads(f.read_text())["translation"] if f.exists() else None


def main():
    outdir = RES / "raw" / "judge"
    outdir.mkdir(parents=True, exist_ok=True)
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())

    planned = len(picks) * len(CONDS) * len(ARMS) * len(JUDGES)
    est = len(picks) * len(CONDS) * len(ARMS) * sum(
        EST_USD_PER_CALL.get(m, 0.01) for m in JUDGES
    )
    done = len(list(outdir.glob("*.json")))
    print(f"plan: {planned} calls ({done} already checkpointed), "
          f"est ${est:.2f}, budget ${BUDGET_USD:.2f}", file=sys.stderr)
    if est > BUDGET_USD:
        sys.exit(f"ABORT: estimate ${est:.2f} exceeds BUDGET_USD=${BUDGET_USD:.2f}")
    if DRY_RUN:
        sys.exit(0)

    scores = []
    spent = 0.0
    for seg in picks:
        ref = (REF / f"{seg}.txt").read_text()
        for cond in CONDS:
            for arm in ARMS:
                zh = get_zh(arm, seg, cond)
                if not zh:
                    print(f"missing zh {arm} {seg} {cond}", file=sys.stderr)
                    continue
                for model in JUDGES:
                    tag = f"{seg}__{cond}__{arm}__{model.replace('/', '_')}"
                    raw_f = outdir / f"{tag}.json"
                    if raw_f.exists():
                        resp = json.loads(raw_f.read_text())
                    else:
                        if spent >= BUDGET_USD:
                            sys.exit(f"ABORT: spent ${spent:.2f} >= BUDGET_USD "
                                     f"(checkpointed work is kept; re-run to resume)")
                        try:
                            resp = judge(model, ref, zh)
                        except Exception as e:
                            print(f"ERR {tag}: {e}", file=sys.stderr)
                            continue
                        # checkpoint before the next paid call: a kill costs one step
                        raw_f.write_text(json.dumps(resp, ensure_ascii=False))
                        spent += call_usd(model, resp)
                        time.sleep(0.5)
                    try:
                        content = resp["candidates"][0]["content"]["parts"][0]["text"]
                        content = content[content.index("{") : content.rindex("}") + 1]
                        j = json.loads(content)
                        scores.append(
                            {"seg": seg, "cond": cond, "arm": arm, "judge": model,
                             "adequacy": int(j["adequacy"]), "tw_locale": int(j["tw_locale"])}
                        )
                    except Exception as e:
                        print(f"PARSE ERR {tag}: {e}", file=sys.stderr)
    (RES / "judge_scores.json").write_text(json.dumps(scores, ensure_ascii=False, indent=1))
    print(f"judged {len(scores)} (arm,seg,cond,judge) tuples; "
          f"this run spent ${spent:.3f} (checkpointed calls were free)")


if __name__ == "__main__":
    main()
