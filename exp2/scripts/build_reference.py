#!/usr/bin/env python3
"""V2 reference builder (adjudicated dual-Gemini merge).

Protocol (documented deviation from naive 2-of-3 majority):
  - Base = token-level agreement of gm35 and gm36 (independent generations
    of different model versions; both produce clean code-mixed text).
  - SM-cmn is NOT given a vote on English-term spelling: its cmn language
    mode is systematically deficient in exactly the dimension this
    experiment measures (e.g. COVID-19 -> "CoffeeNight"); letting it vote
    would corrupt the ground truth. It remains on file as a third opinion.
  - Every gm35/gm36 disagreement is logged to review.md; rulings applied
    via overrides.json (human-authored; slide PDFs are the authority for
    term spelling).
  - Default when both variants are plausible fillers: gm35's reading.

Tokenization: latin word | digit run | single CJK char. Punctuation dropped.
Output: exp2/corpus/reference/{seg}.txt  (tokens joined; latin tokens
space-separated, CJK concatenated)
"""
import difflib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VER = ROOT / "corpus" / "verify"
REF = ROOT / "corpus" / "reference"

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*|\d+(?:\.\d+)?|[㐀-鿿]")


def tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(text)


def join(toks: list[str]) -> str:
    out = []
    for i, t in enumerate(toks):
        latin = t[0].isascii()
        if out and latin and out[-1][-1].isascii():
            out.append(" ")
        out.append(t)
    return "".join(out)


def main():
    REF.mkdir(parents=True, exist_ok=True)
    overrides_path = REF / "overrides.json"
    overrides = json.loads(overrides_path.read_text()) if overrides_path.exists() else {}
    review = ["# V2 reference review — gm35 vs gm36 disagreements\n",
              "Ruling default: gm35; explicit rulings in overrides.json\n"]
    for seg in ["S1", "S2", "S3"]:
        a = tokens((VER / f"{seg}.gm35.txt").read_text())
        b = tokens((VER / f"{seg}.gm36.txt").read_text())
        sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
        merged = []
        review.append(f"\n## {seg}  (gm35={len(a)} toks, gm36={len(b)} toks)\n")
        diffs = 0
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                merged.extend(a[i1:i2])
            else:
                diffs += 1
                ka, kb = join(a[i1:i2]), join(b[j1:j2])
                key = f"{seg}:{diffs}"
                ruling = overrides.get(key)
                if ruling is not None:
                    merged.extend(tokens(ruling))
                    chosen = f"OVERRIDE={ruling!r}"
                else:
                    merged.extend(a[i1:i2])
                    chosen = "gm35(default)"
                review.append(f"- [{key}] gm35='{ka}' | gm36='{kb}' -> {chosen}\n")
        (REF / f"{seg}.txt").write_text(join(merged), encoding="utf-8")
        print(f"{seg}: {len(merged)} tokens, {diffs} disagreement blocks")
    (REF / "review.md").write_text("".join(review), encoding="utf-8")


if __name__ == "__main__":
    main()
