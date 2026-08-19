#!/usr/bin/env python3
"""handoff-v8 §1 F:全地端聽譯鏈端到端。

報告 §3F.3 說「全地端鏈 = Breeze 聽 + Gemma 4 譯」,但標著**未驗證**——
兩端各自的數字都有了,中間從沒接起來過。這支就是把它接起來。

**不重跑聽的那一跳**,直接吃既有的 `exp5/results/raw/<asr_arm>/` 轉寫,
所以這一格不用 GPU、成本只有譯那跳的 API 費。

五條鏈同料比較(輸入音檔完全相同,只有引擎組合不同):

    Xbrz_auto  → gemma-4-26b-a4b-it    全地端候選(兩端都是開源權重)
    Xbrz_auto  → gemini-3.5-flash      只換譯的那跳,隔離「譯」的貢獻
    Xbat_bi    → gemini-3.5-flash      現行產線的形狀(雲端兩跳)
    Xgma_e4b   → gemma-4-26b-a4b-it    **純 Gemma 全地端**(handoff-v9 G)
    Xgma_e4b   → gemini-3.5-flash      換耳朵不換嘴,量第一跳的純效果

指標(exp5 是中英夾雜,所以看**英文在譯文裡活下來沒有**):

    · 英文術語保留率 —— 用 exp5 凍結的 terms.json,tolerant 判定
    · 簡體殘留字數
    · 台灣用語誤用 —— 用 scripts/score.py 的 TW_BAD 清單

用法:
    python3 exp5/scripts/run_chain_translate.py
"""
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT.parent / "scripts"
sys.path.insert(0, str(V1))
from prompts import INTERPRETER_SYSTEM, TRANSLATE_USER_TEMPLATE  # noqa: E402
from score import TW_BAD, SIMPLIFIED_RE  # noqa: E402

G_KEY = os.environ["gemini_key"]
CHAINS = [
    ("Xbrz_auto", "gemma-4-26b-a4b-it", "chain_brz_gemma"),
    ("Xbrz_auto", "gemini-3.5-flash", "chain_brz_gemini"),
    ("Xbat_bi", "gemini-3.5-flash", "chain_sm_gemini"),
    # handoff-v9 G:E4B 當耳朵。前三條的耳朵是 Breeze 或 SM,
    # E4B 是 v8 之後才有資格當第一跳的,從沒接過。
    # chain_e4b_gemma  = **純 Gemma 全地端**(兩端都是 Gemma 4 開源權重)
    # chain_e4b_gemini = 第二跳固定 Gemini,與 chain_brz_gemini 相減
    #                    就是「第一跳的純效果」(任務書 §3 G 第二張表)
    ("Xgma_e4b", "gemma-4-26b-a4b-it", "chain_e4b_gemma"),
    ("Xgma_e4b", "gemini-3.5-flash", "chain_e4b_gemini"),
    # handoff-v9 §4b L:只差標點的 2×2(侷限 16p)。第二跳固定 gemma-4-26b,
    # 兩個來源 arm 由 make_form_variants.py 純機械產生,內容與原轉寫逐字元相同。
    ("Xbrz_punct", "gemma-4-26b-a4b-it", "chain_brzp_gemma"),
    ("Xgma_e4b_nopunct", "gemma-4-26b-a4b-it", "chain_e4bn_gemma"),
    # handoff-v9 §4c M:L 排除了標點,但暴露出沒控制到的變因——**空格**。
    # 這兩條只動空白,一個字元都沒改。
    ("Xgma_e4b_sp", "gemma-4-26b-a4b-it", "chain_e4bsp_gemma"),
    ("Xbrz_nosp", "gemma-4-26b-a4b-it", "chain_brzns_gemma"),
]


def translate(model: str, transcript: str) -> tuple[str, dict, float]:
    body = {
        "systemInstruction": {"parts": [{"text": INTERPRETER_SYSTEM}]},
        "contents": [{"parts": [
            {"text": TRANSLATE_USER_TEMPLATE.format(transcript=transcript)}]}],
        # 機械性任務 → minimal(CLAUDE.md 鐵律 4)。Gemma 4 也吃這個參數。
        "generationConfig": {"temperature": 0.2,
                             "thinkingConfig": {"thinkingLevel": "minimal"}},
    }
    t0 = time.time()
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": G_KEY}, json=body, timeout=600)
    el = time.time() - t0
    r.raise_for_status()
    d = r.json()
    parts = d["candidates"][0]["content"]["parts"]
    # Gemma 4 把 thinking 當一般 part 回傳且不一定標 thought,不濾會拿到
    # 一串英文推理當譯文(docs/gemini-api-lessons.md 記過這個坑)
    txt = "".join(p.get("text", "") for p in parts
                  if "text" in p and not p.get("thought"))
    return txt.strip(), d.get("usageMetadata", {}), round(el, 1)


def main() -> None:
    terms = json.loads((ROOT / "corpus" / "terms.json").read_text())
    picks = json.loads((ROOT / "corpus" / "picks.json").read_text())
    stems = [f"{p['seg']}__{c}" for p in picks for c in ("M0", "M3")]

    summary = {}
    for asr_arm, model, out_arm in CHAINS:
        src = ROOT / "results" / "raw" / asr_arm
        dst = ROOT / "results" / "raw" / out_arm
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "_meta.json").write_text(json.dumps(
            {"chain": f"{asr_arm} → {model}", "asr_arm": asr_arm,
             "translate_model": model, "thinkingLevel": "minimal",
             "temperature": 0.2, "prompt": "INTERPRETER_SYSTEM(凍結)",
             "note": "不重跑 ASR,直接吃既有轉寫"}, ensure_ascii=False, indent=1))

        hit = tot = bad = simp = 0
        for stem in stems:
            f = src / f"{stem}.json"
            if not f.exists():
                continue
            out = dst / f"{stem}.json"
            if out.exists():
                zh = json.loads(out.read_text())["translation"]
            else:
                tx = json.loads(f.read_text())
                tx = tx.get("transcript") or tx.get("input_transcription") or ""
                zh, usage, el = translate(model, tx)
                out.write_text(json.dumps(
                    {"chain": f"{asr_arm} → {model}", "file": f"{stem}.wav",
                     "translation": zh, "elapsed_sec": el, "usage": usage},
                    ensure_ascii=False, indent=1))
                print(f"  [{out_arm}] {stem} {el}s {len(zh)}字", flush=True)
            seg = stem.split("__")[0]
            zl = zh.lower()
            # 譯文不逐次對位,所以每個術語只算一次「有沒有活下來」
            for t in terms[seg]:
                forms = [t["term"].lower()] + [v.lower() for v in t["variants"]]
                tot += 1
                hit += 1 if any(v in zl for v in forms) else 0
            bad += sum(zh.count(w) for w in TW_BAD)
            simp += len(SIMPLIFIED_RE.findall(zh))
        summary[out_arm] = {"chain": f"{asr_arm} → {model}",
                            "term_survival": round(hit / tot, 4) if tot else None,
                            "n_terms": tot, "tw_bad": bad, "simplified": simp}

    (ROOT / "results" / "chain_scores.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"\n{'鏈':<34}{'英文保留':>9}{'台灣用語誤用':>13}{'簡體殘留':>10}")
    for k, v in summary.items():
        print(f"{v['chain']:<34}{v['term_survival']:>9.3f}{v['tw_bad']:>13}{v['simplified']:>10}")
    print("\n→ exp5/results/chain_scores.json")


if __name__ == "__main__":
    main()
