#!/usr/bin/env python3
"""「並排雙跑 + 人工複核」的可行性量化(handoff-v7 的正面產出)。

oracle 說「非即時多跑一個引擎有 +0.03~+0.13 的空間」,但那是**事後最佳選擇**。
真實流程是:兩個引擎都跑 → 標出分歧 → 人看分歧處。所以要先回答兩件事:

1. **複核負擔有多大?** 兩份稿子的分歧佔全文多少?這決定人力成本。
2. **人看了有沒有用?** 在分歧的專名上,兩邊各對多少次?
   若某一邊在分歧處幾乎總是對的,那就不必人看——直接用那一邊即可,
   **這個建議就不成立**。要接近五五開,複核才真的在做選擇。

純計算,不呼叫任何 API。
"""
import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

# exp1 的非即時兩隻:預設 Gemini 批次 vs SM 批次 + 詞表(§8b 原本量的那一對)。
# **2026-08-20 起可以換對**:oracle_report §8d 重算互補性之後,建議配對改成
# Gemini 批次 + **Scribe 批次**(最佳單一 arm 從 N3 0.597 升到 0.657),
# 所以複核負擔也要在**新的那一對**上重算——不能沿用舊配對的 11% / 49%。
#     python3 analysis/dual_run.py                      # Abat vs Cbplus(原配對)
#     python3 analysis/dual_run.py Abat Sbat_ja_full    # 新配對
A, B = "Abat", "Cbplus"
if len(sys.argv) == 3:
    A, B = sys.argv[1], sys.argv[2]
LABEL = {"Abat": "Gemini 批次", "Cbplus": "SM 批次+詞表",
         "Sbat_ja_full": "Scribe 批次+完整詞表", "Sbat_ja": "Scribe 批次+50 詞"}
CONDS = ["N0", "N1", "N2", "N3", "N4"]
TOKEN = re.compile(r"[0-9A-Za-z]+|[^\s0-9A-Za-z]")

# ── 分歧分類規則:**先於計算寫死**,不看到數字才決定怎麼分 ────────────────
#
# 形式分歧 = 兩邊講的是同一件事,只是寫法不同 → 不需要複核
# 實質分歧 = 詞彙/專名/語意不同 → 要複核
#
# 刻意保守:**只有能明確判定為形式的才歸形式**,其餘一律算實質。
# 這讓複核工作量的估計偏高而不是偏低——寧可承諾得保守。
PUNCT = set('、。,.!?!?・…‥「」『』()()〈〉《》【】〔〕—ー–-~〜:;:;"\'“”‘’`,')
# 贅詞:Gemini 批次會順手刪、SM 是逐字派,兩邊處理不同會產生大量假分歧。
# 日文(exp1 語料)與中文(外推到會議/訪談時會用到)都先列進來。
FILLER = {
    # 日文
    "えー", "ええ", "えっと", "えと", "あの", "あのー", "その", "まあ", "まー",
    "なんか", "ちょっと", "ね", "ねー", "よ", "さ", "うん", "はい",
    # 中文
    "嗯", "啊", "呃", "欸", "齁", "喔", "唉", "那個", "這個", "就是",
}
KANSUJI = str.maketrans("〇零一二三四五六七八九", "00123456789")


def norm_token(t: str) -> str | None:
    """→ 正規化後的 token;回傳 None 代表「這個 token 不影響語意」。"""
    t = unicodedata.normalize("NFKC", t).lower()   # 全半形、數字寫法
    if not t or t in PUNCT or t.isspace():
        return None
    if t in FILLER:
        return None
    if all(c in "〇零一二三四五六七八九十" for c in t) and len(t) <= 2:
        t = t.translate(KANSUJI)                   # 漢数字 vs アラビア数字
    return t


def toks(s: str) -> list[str]:
    return TOKEN.findall(s or "")


def norm_toks(s: str) -> list[str]:
    return [n for t in toks(s) if (n := norm_token(t)) is not None]


def divergence(ta: list[str], tb: list[str]) -> tuple[int, int]:
    """→ (分歧 token 數, 總 token 數)"""
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    tot = diff = 0
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        n = max(i2 - i1, j2 - j1)
        tot += n
        if op != "equal":
            diff += n
    return diff, tot


def transcript(arm: str, seg: str, cond: str) -> str:
    f = ROOT / "results/raw" / arm / f"{seg}__{cond}.json"
    if not f.exists():
        return ""
    d = json.loads(f.read_text())
    return d.get("transcript") or d.get("input_transcription") or ""


def main() -> None:
    segs = list(json.loads((ROOT / "corpus/picks.json").read_text()))
    outcomes = json.loads((ROOT / "results/noun_outcomes.json").read_text())

    # ① 複核負擔:全部分歧 vs 實質分歧
    #    全部 = 原始 token 流的 diff(含標點、贅詞、寫法差異)
    #    實質 = 正規化後 token 流的 diff(去掉上述形式差異)
    load, load_sub = {}, {}
    for cond in CONDS:
        d = t = ds = ts = 0
        for seg in segs:
            xa, xb = transcript(A, seg, cond), transcript(B, seg, cond)
            if not xa or not xb:
                continue
            n_d, n_t = divergence(toks(xa), toks(xb))
            n_ds, n_ts = divergence(norm_toks(xa), norm_toks(xb))
            d += n_d; t += n_t; ds += n_ds; ts += n_ts
        load[cond] = round(d / t, 4) if t else None
        load_sub[cond] = round(ds / ts, 4) if ts else None

    # ② 分歧處誰對:只看兩邊判定不同的專名
    idx = {}
    for r in outcomes:
        if r["arm"] in (A, B):
            idx.setdefault((r["cond"], r["seg"], r["noun"]), {})[r["arm"]] = r["tolerant"]
    split = {}
    for cond in CONDS:
        a_only = b_only = agree_hit = agree_miss = 0
        for (c, _s, _n), per in idx.items():
            if c != cond or A not in per or B not in per:
                continue
            ha, hb = per[A], per[B]
            if ha and hb:
                agree_hit += 1
            elif not ha and not hb:
                agree_miss += 1
            elif ha:
                a_only += 1
            else:
                b_only += 1
        n_split = a_only + b_only
        split[cond] = {
            f"只有 {LABEL.get(A, A)}對": a_only, f"只有 {LABEL.get(B, B)}對": b_only,
            "兩邊都對": agree_hit, "兩邊都錯": agree_miss,
            "分歧數": n_split,
            f"分歧中 {LABEL.get(A, A)}對的比例": round(a_only / n_split, 3) if n_split else None,
        }

    out = {"note": "並排雙跑的複核負擔與分歧處勝負。純計算,無 API 呼叫。",
           "arms": [A, B],
           "classification_rules": {
               "形式分歧": "標點、空白、全半形、漢数字/アラビア数字、贅詞(FILLER 清單)",
               "實質分歧": "其餘一律算實質(保守:寧可高估複核量)",
               "filler_list": sorted(FILLER)},
           "text_divergence_ratio": load,
           "substantive_divergence_ratio": load_sub,
           "proper_noun_split": split}
    (ROOT / ("results/dual_run.json" if (A, B) == ("Abat", "Cbplus")
                     else f"results/dual_run_{A}_{B}.json")).write_text(
        json.dumps(out, ensure_ascii=False, indent=1))

    print(f"並排雙跑:{A} vs {B}\n")
    print(f"cond  全分歧  實質分歧  專名分歧  只有A對  只有B對  A佔比"
          f"   (A={LABEL.get(A, A)}, B={LABEL.get(B, B)})")
    for c in CONDS:
        s = split[c]
        ratio = s[f"分歧中 {LABEL.get(A, A)}對的比例"]
        print(f"{c}  {load[c]:>7.3f}  {load_sub[c]:>8.3f}  {s['分歧數']:>8}"
              f"  {s[f'只有 {LABEL.get(A, A)}對']:>12}  {s[f'只有 {LABEL.get(B, B)}對']:>8}  "
              f"{'—' if ratio is None else format(ratio, '.2f'):>9}")
    print(f"\n→ results/dual_run{'' if (A, B) == ('Abat', 'Cbplus') else f'_{A}_{B}'}.json")


if __name__ == "__main__":
    main()
