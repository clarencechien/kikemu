#!/usr/bin/env python3
"""V2 scoring: English-term recall, failure modes, over-translation, CER.

Judgment rules (frozen in make_terms.py):
  strict   — case-insensitive exact term string in the hypothesis
  tolerant — strict, or hyphen/space folding, or frozen variants

Failure-mode classification for each missed instance (mechanical, via
token alignment ref->hyp):
  F1 諧音化   aligned hyp span is CJK text (English became Chinese)
  F2 誤認他字 aligned hyp span contains other latin text
  F4 遺漏     aligned hyp span is empty
  F3 語言崩潰 file-level flag: hyp CJK ratio < 0.3 while ref CJK ratio > 0.6
              (whole-language misjudgment; instance classes still recorded)

Over-translation (translation layer): fraction of term TYPES whose English
string is absent (case-insensitive, variants folded) from the zh output,
reported separately for no_translate vs may_translate classes.

CER: hyp and ref folded Simplified->Traditional (OpenCC s2t), latin
lowercased, punctuation stripped; token-level edit distance where a token
is one CJK char or one latin word (so an English word counts as 1 unit).
"""
import difflib
import json
import re
import sys
from collections import Counter
from pathlib import Path

from opencc import OpenCC

V1 = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(V1))

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
REF = ROOT / "corpus" / "reference"

S2T = OpenCC("s2t")
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*|\d+(?:\.\d+)?|[㐀-鿿]")
CONDS = ["M0", "M1", "M2", "M3"]


def fold_text(text: str) -> str:
    return S2T.convert(text)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in TOKEN_RE.findall(fold_text(text))]


def term_forms(t: dict) -> list[str]:
    return [t["term"].lower()] + t["variants"]


def contains(hyp_join: str, form: str) -> bool:
    return form.lower().replace("-", " ") in hyp_join or form.lower() in hyp_join


def hyp_join(hyp_tokens: list[str]) -> str:
    out = []
    for t in hyp_tokens:
        if out and t[0].isascii() and out[-1][-1].isascii():
            out.append(" ")
        out.append(t)
    return "".join(out).lower()


def find_instances(ref_toks: list[str], term: str) -> list[tuple[int, int]]:
    """Positions (start, end) of each term instance in the ref token list."""
    words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9'\-]*|\d+", term)]
    hits = []
    n = len(words)
    for i in range(len(ref_toks) - n + 1):
        if ref_toks[i : i + n] == words:
            hits.append((i, i + n))
    return hits


def classify_miss(ref_toks, hyp_toks, span, opcodes) -> str:
    i1, i2 = span
    hyp_span = []
    for op, a1, a2, b1, b2 in opcodes:
        if a2 <= i1 or a1 >= i2:
            continue
        hyp_span.extend(hyp_toks[b1:b2])
    if not hyp_span:
        return "F4"
    if any(t[0].isascii() and t[0].isalpha() for t in hyp_span):
        return "F2"
    return "F1"


def cjk_ratio(toks):
    if not toks:
        return 0.0
    return sum(1 for t in toks if not t[0].isascii()) / len(toks)


def main():
    terms_all = json.loads((ROOT / "corpus" / "terms.json").read_text())
    arms = [d.name for d in (RES / "raw").iterdir()
            if d.is_dir() and "translate" not in d.name]
    rows, inst_rows = [], []
    for arm in sorted(arms):
        for f in sorted((RES / "raw" / arm).glob("*.json")):
            # 每個 arm 目錄可以放 _meta.json 記環境與模型 revision(x-breeze 這樣做),
            # 那不是一筆結果——只有 <seg>__<cond>.json 才是
            if "__" not in f.stem:
                continue
            seg, cond = f.stem.split("__")
            d = json.loads(f.read_text())
            hyp_text = d.get("transcript") or d.get("input_transcription") or ""
            ref_text = (REF / f"{seg}.txt").read_text()
            ref_toks = tokens(ref_text)
            hyp_toks = tokens(hyp_text)
            hj = hyp_join(hyp_toks)
            sm = difflib.SequenceMatcher(None, ref_toks, hyp_toks, autojunk=False)
            ops = sm.get_opcodes()
            f3 = cjk_ratio(hyp_toks) < 0.3 and cjk_ratio(ref_toks) > 0.6

            n_inst = hits_s = hits_t = 0
            fails = Counter()
            for t in terms_all[seg]:
                spans = find_instances(ref_toks, t["term"])
                strict_hit = t["term"].lower() in hj
                tol_hit = strict_hit or any(contains(hj, v) for v in t["variants"])
                for span in spans:
                    n_inst += 1
                    hits_s += strict_hit
                    hits_t += tol_hit
                    if not tol_hit:
                        fails[classify_miss(ref_toks, hyp_toks, span, ops)] += 1
                inst_rows.append({"arm": arm, "seg": seg, "cond": cond,
                                  "term": t["term"], "n": len(spans),
                                  "strict": int(strict_hit), "tolerant": int(tol_hit)})
            # token-level CER
            dist = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in ops if op != "equal")
            rows.append({
                "arm": arm, "seg": seg, "cond": cond,
                "n_inst": n_inst,
                "recall_strict": round(hits_s / n_inst, 4) if n_inst else None,
                "recall_tolerant": round(hits_t / n_inst, 4) if n_inst else None,
                "F1": fails["F1"], "F2": fails["F2"], "F4": fails["F4"],
                "F3_flag": bool(f3),
                "ter": round(dist / len(ref_toks), 4),
                "hyp_cjk_ratio": round(cjk_ratio(hyp_toks), 3),
            })

    # over-translation on translation outputs
    ot_rows = []
    for d_ in sorted((RES / "raw").iterdir()):
        if "_translate_" not in d_.name:
            continue
        arm, ptag = d_.name.rsplit("_translate_", 1)
        for f in sorted(d_.glob("*.json")):
            seg, cond = f.stem.split("__")
            zh = json.loads(f.read_text())["translation"]
            zl = zh.lower()
            src = json.loads((RES / "raw" / arm / f.name).read_text())
            src_hj = hyp_join(tokens(src.get("transcript", "")))
            res = {}
            for cls in ("no_translate", "may_translate"):
                ts = [t for t in terms_all[seg] if t["class"] == cls]
                # only terms the ASR actually got (can't over-translate what
                # was never heard in English)
                heard = [t for t in ts
                         if t["term"].lower() in src_hj or any(contains(src_hj, v) for v in t["variants"])]
                gone = [t["term"] for t in heard
                        if t["term"].lower() not in zl
                        and not any(v in zl for v in t["variants"])]
                res[cls] = {"heard": len(heard), "translated_away": len(gone), "gone": gone}
            ot_rows.append({"arm": arm, "prompt": ptag, "seg": seg, "cond": cond, **{
                f"{c}_{k}": v for c, r in res.items() for k, v in r.items() if k != "gone"}})
            ot_rows[-1]["gone_terms"] = {c: res[c]["gone"] for c in res}

    (RES / "scores.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    (RES / "term_outcomes.json").write_text(json.dumps(inst_rows, ensure_ascii=False))
    (RES / "overtranslation.json").write_text(json.dumps(ot_rows, ensure_ascii=False, indent=1))
    print(f"scored {len(rows)} files, {len(inst_rows)} term rows, {len(ot_rows)} translation rows")


if __name__ == "__main__":
    main()
